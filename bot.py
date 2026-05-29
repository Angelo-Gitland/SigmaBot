import discord
from discord import app_commands
import re
import os
from datetime import timedelta, timezone, datetime
import firebase_admin
from firebase_admin import credentials, firestore
import aiohttp
import subprocess
import io

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
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def on_ready(self):
        await self.tree.sync()
        print(f"Logged in as {self.user} | Synced commands!")

    async def on_disconnect(self):
        print("[DISCONNECT] Bot disconnected from Discord!")

    async def on_resumed(self):
        print("[RECONNECT] Bot reconnected successfully!")

    async def on_error(self, event, *args, **kwargs):
        print(f"[ERROR] An error occurred in event {event}")
        import traceback
        traceback.print_exc()

    async def on_message(self, message):
        if message.author.bot:
            return

        doc_ref = db.collection("sticky").document(str(message.channel.id))
        doc = doc_ref.get()

        if not doc.exists:
            return

        data = doc.to_dict()
        if not data.get("enabled", True):
            return

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

@client.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"[ERROR] Error occurred: {error}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send("An error occurred while running this command!", ephemeral=True)
        else:
            await interaction.response.send_message("An error occurred while running this command!", ephemeral=True)
    except Exception:
        pass

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

@kick.error
async def kick_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

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

@ban.error
async def ban_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

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

@purge.error
async def purge_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

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

@timeout.error
async def timeout_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

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

@warn.error
async def warn_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

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

@stick_create.error
async def stick_create_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

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

@stick_remove.error
async def stick_remove_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

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

@stick_list.error
async def stick_list_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

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

@nick.error
async def nick_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

@client.tree.command(name="embed-create", description="Create an embed message.")
@app_commands.describe(
    message="Set the embed message.",
    color="Set the embed color (e.g #5865F2).",
    channel="Channel where embed message will send (Optional).",
    server_thumbnail="Include server thumbnail in embed message (Optional)."
)
@app_commands.checks.has_permissions(manage_messages=True)
async def embed_create(
    interaction: discord.Interaction,
    message: str,
    color: str,
    channel: discord.TextChannel = None,
    server_thumbnail: bool = False
):
    await interaction.response.defer(ephemeral=True)

    target_channel = channel if channel else interaction.channel

    try:
        color = color.strip().lstrip("#")
        embed_color = int(color, 16)
    except ValueError:
        await interaction.followup.send(
            "❌ Invalid color! Please use a valid hex color (e.g `#5865F2`).",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        description=message,
        color=embed_color
    )

    if server_thumbnail and interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)

    try:
        await target_channel.send(embed=embed)
        await interaction.followup.send(
            f"✅ Embed sent in {target_channel.mention}!",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "I do not have permission to send embed messages in this channel!",
            ephemeral=True
        )

@embed_create.error
async def embed_create_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You do not have permission to use this command!", ephemeral=True)

@client.tree.command(name="pastebin", description="Paste a code into pastebin website as guest.")
@app_commands.describe(
    file="Attach a .txt or .lua file only."
)
async def pastebin(
    interaction: discord.Interaction,
    file: discord.Attachment
):
    await interaction.response.defer(ephemeral=True)

    if not file.filename.endswith((".txt", ".lua")):
        await interaction.followup.send(
            "❌ Only `.txt` or `.lua` files are allowed!",
            ephemeral=True
        )
        return

    paste_title = file.filename.rsplit(".", 1)[0]

    async with aiohttp.ClientSession() as session:
        file_content = await file.read()
        paste_code = file_content.decode("utf-8")

        data = {
            "api_option": "paste",
            "api_dev_key": os.getenv("PASTEBIN_API_KEY"),
            "api_paste_code": paste_code,
            "api_paste_name": paste_title,
            "api_paste_private": "1",
            "api_paste_format": "lua" if file.filename.endswith(".lua") else "text"
        }

        async with session.post("https://pastebin.com/api/api_post.php", data=data) as resp:
            result = await resp.text()

            if result.startswith("https://pastebin.com/"):
                embed = discord.Embed(
                    title="Pastebin Upload Successful!",
                    description=f"**Title:** {paste_title}\n**Link:** {result}",
                    color=0x02A8F3
                )
                await interaction.followup.send(embed=embed, ephemeral=False)
            else:
                await interaction.followup.send(
                    f"❌ Failed to upload to Pastebin! Error: `{result}`",
                    ephemeral=True
                )

@client.tree.command(name="execute-script", description="Execute a lua script code.")
@app_commands.describe(
    file="Attach only a .txt or .lua file.",
    code="Execute lua code by text."
)
async def execute_script(
    interaction: discord.Interaction,
    file: discord.Attachment = None,
    code: str = None
):
    await interaction.response.defer(ephemeral=False)

    if not file and not code:
        await interaction.followup.send("You must provide either a file or code!")
        return

    lua_code = ""

    if file:
        if not file.filename.endswith((".txt", ".lua")):
            await interaction.followup.send("Only .txt or .lua files are allowed!")
            return
        
        file_content = await file.read()
        lua_code = file_content.decode("utf-8")
    elif code:
        lua_code = code

    try:
        process = subprocess.run(
            ["lua", "-e", lua_code],
            capture_output=True,
            text=True,
            timeout=10
        )
        output = process.stdout
        if process.stderr:
            output += "\n" + process.stderr
        
        if not output.strip():
            output = "Code executed successfully with no output."

    except Exception as e:
        output = str(e)

    full_output_message = f"```Output\n{output}\n```"

    if len(full_output_message) <= 2000:
        await interaction.followup.send(f"Successfully executed! Outputs below!\n{full_output_message}")
    else:
        file_obj = discord.File(io.BytesIO(output.encode("utf-8")), filename="output.txt")
        await interaction.followup.send("Successfully executed! Outputs below!", file=file_obj)

client.run(os.getenv("TOKEN"))
