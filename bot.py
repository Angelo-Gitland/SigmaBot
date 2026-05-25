import discord
from discord import app_commands
import re
import os
import time
import firebase_admin
from firebase_admin import credentials, firestore

cred = credentials.Certificate({
    "type": "service_account",
    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
    "private_key": os.getenv("FIREBASE_PRIVATE_KEY").replace("\\n", "\n"),
    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
    "token_uri": "https://oauth2.googleapis.com/token"
})
firebase_admin.initialize_app(cred)
db = firestore.client()

class BotClient(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.tree = app_commands.CommandTree(self)

    async def on_ready(self):
        await self.tree.sync()
        print(f"Logged in as {self.user} | Synced commands!")

    async def on_message(self, message):
        if message.author.bot:
            return

        afk_ref = db.collection("afk").document(str(message.author.id))
        afk_doc = afk_ref.get()
        if afk_doc.exists:
            data = afk_doc.to_dict()
            await message.channel.send(f"{message.author.mention} Welcome back! You were last seen <t:{data['time']}:R>.")
            afk_ref.delete()

        for mention in message.mentions:
            if mention.id == message.author.id:
                continue
            
            afk_data = db.collection("afk").document(str(mention.id)).get()
            if afk_data.exists:
                data = afk_data.to_dict()
                await message.channel.send(f"{mention.name} is currently afk. They were last seen <t:{data['time']}:R>.\n- Reason: {data['reason']}")

        doc_ref = db.collection("sticky").document(str(message.channel.id))
        doc = doc_ref.get()

        if doc.exists:
            data = doc.to_dict()
            if data.get("enabled", True):
                message_count = data.get("message_count", 0) + 1
                duration = data.get("duration", 5)
                sticky_message = data.get("message", "")
                last_message_id = data.get("last_message_id", None)

                if message_count >= duration:
                    if last_message_id:
                        try:
                            old_msg = await message.channel.fetch_message(int(last_message_id))
                            await old_msg.delete()
                        except (discord.NotFound, discord.Forbidden):
                            pass

                    new_msg = await message.channel.send(f"**Stickied Message:**\n\n{sticky_message}")
                    doc_ref.update({
                        "last_message_id": str(new_msg.id),
                        "message_count": 0
                    })
                else:
                    doc_ref.update({"message_count": message_count})

client = BotClient()

@client.tree.command(name="afk", description="Set your status as afk.")
@app_commands.describe(reason="Set a reason why are you afk? (Optional)")
async def afk(interaction: discord.Interaction, reason: str = "No reason provided"):
    db.collection("afk").document(str(interaction.user.id)).set({
        "reason": reason,
        "time": int(time.time())
    })
    await interaction.response.send_message(f"{interaction.user.mention}, your AFK status has been set.", ephemeral=True)

@client.tree.command(name="kick", description="kick a user")
@app_commands.describe(user="Select a members to kick.", reason="Reason to kick (Optional).")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = None):
    if reason is None:
        reason = "No reason provided"
    
    try:
        dm_message = f"## ⚠️ You have been kicked from {interaction.guild.name}!\n\n**Reason: {reason}**\n**Kicked by: {interaction.user.name}**"
        await user.send(dm_message)
    except discord.Forbidden:
        pass

    try:
        await user.kick(reason=f"Kicked by {interaction.user}: {reason}")
        await interaction.response.send_message(f"Successfully kicked {user.mention}. Reason: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("I do not have permissions to kick this user!", ephemeral=True)

@client.tree.command(name="ban", description="Ban a user.")
@app_commands.describe(user="User to ban.", reason="Reason to ban (Optional).", duration="Duration to hide message activities for this user (Optional).")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = None, duration: str = None):
    if reason is None:
        reason = "No reason provided."
    
    display_duration = duration if duration else "No time provided"
    delete_seconds = parse_duration_to_seconds(duration) if duration else 0

    try:
        dm_message = f"## ⚠️ You have been banned from {interaction.guild.name}\n\n**Reason: {reason}**\n**Banned by {interaction.user.name}**\n**Duration: {display_duration}**"
        await user.send(dm_message)
    except discord.Forbidden:
        pass

    try:
        await user.ban(delete_message_seconds=delete_seconds, reason=f"Banned by {interaction.user}: {reason}")
        await interaction.response.send_message(f"Successfully banned {user.mention}. Reason: {reason}")
    except discord.Forbidden:
        await interaction.response.send_message("I do not have permission to ban this user!", ephemeral=True)

def parse_duration_to_seconds(duration_str: str) -> int:
    if not duration_str:
        return 0
    match = re.match(r"^(\d+)([smhd])$", duration_str.strip().lower())
    if not match:
        return 0
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == 's':
        return amount
    elif unit == 'm':
        return amount * 60
    elif unit == 'h':
        return amount * 3600
    elif unit == 'd':
        return amount * 86400
    return 0

@client.tree.command(name="say", description="Let bot send the message of what you say!")
@app_commands.describe(message="The message you want the bot to repeat.")
async def say(interaction: discord.Interaction, message: str):
    await interaction.response.send_message("Message sent!", ephemeral=True)
    await interaction.channel.send(message)

@client.tree.command(name="purge", description="Clear messages in this channel.")
@app_commands.describe(
    number_of_messages="Number of messages that you want to delete.",
    filter_by_user="Delete messages sends by the user (Optional).",
    filter_by_role="Delete messages by the user with the selected role (Optional).",
    filter_by_bots="Delete messages sends by the bots (Optional)."
)
@app_commands.checks.has_permissions(manage_messages=True)
async def purge(
    interaction: discord.Interaction,
    number_of_messages: int,
    filter_by_user: discord.Member = None,
    filter_by_role: discord.Role = None,
    filter_by_bots: bool = False
):
    await interaction.response.defer(ephemeral=True)

    def check(msg):
        if filter_by_user and msg.author != filter_by_user:
            return False
        if filter_by_role and filter_by_role not in msg.author.roles:
            return False
        if filter_by_bots and not msg.author.bot:
            return False
        return True

    try:
        deleted = await interaction.channel.purge(limit=number_of_messages, check=check)
        await interaction.followup.send(f"Successfully deleted **{len(deleted)}** message(s).", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("I do not have permission to purge messages!", ephemeral=True)

@client.tree.command(name="timeout", description="Timeout a user.")
@app_commands.describe(
    user="User to timeout.",
    reason="Reason for timeout (Optional).",
    duration="Time/Duration for timeout, e.g 1d, 24h, 67m, 100s (Optional)."
)
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = None,
    duration: str = None
):
    await interaction.response.defer(ephemeral=True)

    display_reason = reason if reason else "No reason provided."
    display_duration = duration if duration else "No time provided."
    timeout_seconds = parse_duration_to_seconds(duration) if duration else 0

    try:
        dm_embed = discord.Embed(
            description=(
                f"**Reason:** {display_reason}\n"
                f"**Duration:** {display_duration}\n"
                f"**Timed out by:** {interaction.user.name} > {interaction.user.display_name}"
            ),
            color=0xFF6B00
        )
        dm_embed.title = f"⚠️ You were timed out in {interaction.guild.name}"
        dm_embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)

        if timeout_seconds > 0:
            await user.timeout(timedelta(seconds=timeout_seconds), reason=f"Timed out by {interaction.user}: {display_reason}")
        else:
            await user.timeout(timedelta(minutes=5), reason=f"Timed out by {interaction.user}: {display_reason}")

        try:
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        await interaction.followup.send(
            f"Successfully timed out {user.mention}. Reason: {display_reason} | Duration: {display_duration}",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.followup.send(
            "I do not have permission to timeout this user! Check my role and ensure my role is higher than this role!",
            ephemeral=True
        )

@client.tree.command(name="warn", description="Warn a user.")
@app_commands.describe(
    user="User to warning.",
    reason="Reason for warning (Optional)."
)
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = None
):
    await interaction.response.defer(ephemeral=True)

    display_reason = reason if reason else "No reason provided!"

    try:
        dm_embed = discord.Embed(
            description=(
                f"**Reason:** {display_reason}\n"
                f"**Warned by:** {interaction.user.name} > {interaction.user.display_name}"
            ),
            color=0xFFCC00
        )
        dm_embed.title = f"⚠️ You were warned in {interaction.guild.name}!"
        dm_embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)

        try:
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        await interaction.followup.send(
            f"Successfully warned {user.mention}. Reason: {display_reason}",
            ephemeral=True
        )

    except Exception:
        await interaction.followup.send(
            "I do not have permission to warn this user! Check my role and ensure my role is higher than this role!",
            ephemeral=True
        )

@client.tree.command(name="ping", description="Show bot latency.")
async def ping(interaction: discord.Interaction):
    latency_ms = round(client.latency * 1000)
    guild_count = len(client.guilds)
    node = "Railway-US-West"

    await interaction.response.send_message(
        f"🏓 PONG!\n"
        f"**Cluster 436:** {latency_ms}ms\n"
        f"**Shard 6984:** {latency_ms}ms\n"
        f"**Guild:** {guild_count}\n"
        f"**Node:** {node}"
    )

@client.tree.command(name="stick-create", description="Create a sticky message on the channel.")
@app_commands.describe(
    channel="Select a channel where to add sticky message.",
    message="Set sticky message.",
    duration="Number of messages to trigger sticky (Optional, default is 5)."
)
@app_commands.checks.has_permissions(manage_messages=True)
async def stick_create(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    message: str,
    duration: int = 5
):
    await interaction.response.defer(ephemeral=True)

    doc_ref = db.collection("sticky").document(str(channel.id))
    doc_ref.set({
        "message": message,
        "duration": duration,
        "enabled": True,
        "last_message_id": None,
        "message_count": 0
    })

    await interaction.followup.send(
        f"✅ Sticky message set in {channel.mention}!\n"
        f"**Message:** {message}\n"
        f"**Trigger every:** {duration} messages",
        ephemeral=True
    )

@client.tree.command(name="stick-remove", description="Remove stick message from a specific channel.")
@app_commands.describe(
    channel="Select a channel where stick messages will be deleted."
)
@app_commands.checks.has_permissions(manage_messages=True)
async def stick_remove(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    await interaction.response.defer(ephemeral=True)

    doc_ref = db.collection("sticky").document(str(channel.id))
    doc = doc_ref.get()

    if not doc.exists:
        await interaction.followup.send(
            f"❌ No sticky message found in {channel.mention}!",
            ephemeral=True
        )
        return

    data = doc.to_dict()
    last_message_id = data.get("last_message_id", None)
    if last_message_id:
        try:
            old_msg = await channel.fetch_message(int(last_message_id))
            await old_msg.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

    doc_ref.delete()
    await interaction.followup.send(
        f"✅ Sticky message removed from {channel.mention}!",
        ephemeral=True
    )

@client.tree.command(name="stick-list", description="Get the list of channels with stick messages.")
@app_commands.checks.has_permissions(manage_messages=True)
async def stick_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    docs = db.collection("sticky").stream()
    entries = []

    for doc in docs:
        channel_id = int(doc.id)
        channel = interaction.guild.get_channel(channel_id)
        if channel:
            entries.append(f"- #{channel.name} | Id: {channel_id}")

    if not entries:
        await interaction.followup.send(
            "❌ No sticky messages found in this server!",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="📌 Sticky Messages List",
        description="\n".join(entries),
        color=0x5865F2
    )
    embed.set_footer(text=f"Total: {len(entries)} sticky channel(s)")

    await interaction.followup.send(embed=embed, ephemeral=True)

@client.tree.command(name="nick", description="Change user/bots nickname.")
@app_commands.describe(
    user="User to set nickname.",
    nickname="Change user nickname."
)
@app_commands.checks.has_permissions(manage_nicknames=True)
async def nick(
    interaction: discord.Interaction,
    user: discord.Member,
    nickname: str
):
    await interaction.response.defer(ephemeral=True)

    try:
        old_nick = user.display_name
        await user.edit(nick=nickname)
        await interaction.followup.send(
            f"✅ Successfully changed **{old_nick}**'s nickname to **{nickname}**!",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "I do not have permission to change nickname of this user!",
            ephemeral=True
        )

client.run(os.getenv("TOKEN"))
