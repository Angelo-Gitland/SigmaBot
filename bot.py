import discord
from discord import app_commands
import random
import string
import re
import io
import hashlib
import time
import asyncio
import os

class BotClient(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.tree = app_commands.CommandTree(self)
        self.honeypots = {}
        self.honeypot_counts = {}
        self.honeypot_panels = {}

    async def on_ready(self):
        await self.tree.sync()
        print(f"Logged in as {self.user}")

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if message.channel.id not in self.honeypots:
            return

        config = self.honeypots[message.channel.id]

        # Allow the whitelisted user
        if config["allowed"] and message.author.id == config["allowed"].id:
            return

        guild = message.guild
        user = message.author
        punishment = config["punishment"]

        # Delete the message
        try:
            await message.delete()
        except discord.Forbidden:
            pass

        # DM the user
        try:
            dm_msg = (
                f"**⚠️ You sent a message in the honeypot channel! "
                f"{user.mention} You have been {punishment}ned from {guild.name}!**\n\n"
                f"[Support Us!](https://discord.gg/kfXTWpk7nC)"
            )
            await user.send(dm_msg)
        except discord.Forbidden:
            pass

        # Apply punishment
        try:
            if punishment == "ban":
                await guild.ban(user, reason="Triggered honeypot channel.")
            else:
                await guild.kick(user, reason="Triggered honeypot channel.")

            self.honeypot_counts[message.channel.id] = self.honeypot_counts.get(message.channel.id, 0) + 1
            count = self.honeypot_counts[message.channel.id]
            label = "Kicks" if punishment == "kick" else "Bans"

            panel_info = self.honeypot_panels.get(message.channel.id)
            if panel_info:
                try:
                    ch = self.get_channel(message.channel.id)
                    panel_msg = await ch.fetch_message(panel_info["message_id"])
                    updated_embed = panel_msg.embeds[0]
                    updated_embed.set_field_at(0, name=f"🥚 {label}", value=str(count), inline=True)
                    await panel_msg.edit(embed=updated_embed)
                except Exception:
                    pass

        except discord.Forbidden:
            ch = self.get_channel(message.channel.id)
            if ch:
                err_embed = discord.Embed(
                    description=f"**{user.name} triggered the honeypot channel, but I do not have permission to {punishment} them! Please ensure my role is higher than them.**",
                    color=0xFF4444
                )
                err_embed.title = "Permission Required!"
                await ch.send(embed=err_embed)

client = BotClient()

_CHARS = string.ascii_letters

def rand_name(length: int = 12) -> str:
    return "_" + "".join(random.choices(_CHARS, k=length))

def rand_number_expr(n: int) -> str:
    ops = [
        lambda: f"({n + random.randint(1,99)} - {random.randint(1,99) + (n + random.randint(1,99)) - n - random.randint(1,99)})",
        lambda: f"(({n * 7}) // 7)" if n != 0 else "0",
        lambda: f"math.floor({float(n) + 0.9999})" if isinstance(n, int) else str(n),
    ]
    return random.choice(ops)()

def encode_string_xor(s: str, key: int) -> str:
    encoded = [b ^ (key & 0xFF) for b in s.encode("utf-8")]
    return "{" + ",".join(str(b) for b in encoded) + "}"

def make_junk_block() -> str:
    junk_templates = [
        lambda: f"local {rand_name()} = math.huge * 0",
        lambda: f"local {rand_name()} = (function() return nil end)()",
        lambda: f"local {rand_name()} = tostring({random.randint(0,9999)})",
        lambda: f"local {rand_name()} = type(nil)",
    ]
    return random.choice(junk_templates)()

def rename_variables(code: str) -> str:
    pattern = re.compile(r'\blocal\s+([a-zA-Z_][a-zA-Z0-9_]*)\b')
    names = set(pattern.findall(code))
    lua_keywords = {
        "and","break","do","else","elseif","end","false","for",
        "function","if","in","local","nil","not","or","repeat",
        "return","then","true","until","while","math","string",
        "table","bit32","pcall","xpcall","pairs","ipairs","next",
        "select","type","tostring","tonumber","rawget","rawset",
        "rawequal","rawlen","setmetatable","getmetatable","print",
        "warn","error","assert","require","task","game","workspace",
        "script","_G","os","tick","wait","spawn","coroutine",
    }
    mapping = {}
    for name in names:
        if name not in lua_keywords and not name.startswith("_"):
            mapping[name] = rand_name(10)
    for old, new in mapping.items():
        code = re.sub(r'\b' + re.escape(old) + r'\b', new, code)
    return code

def obfuscate_strings(code: str, xor_key: int, decoder_var: str) -> str:
    pattern = r'"((?:[^"\\]|\\.)*?)"|\'((?:[^\'\\]|\\.)*?)\''
    def replacer(m):
        s = m.group(1) if m.group(1) is not None else m.group(2)
        if not s:
            return '""'
        try:
            encoded = encode_string_xor(s, xor_key)
            return f'{decoder_var}({encoded})'
        except Exception:
            return m.group(0)
    return re.sub(pattern, replacer, code)

def build_number_mutation_layer(code: str) -> str:
    def mutate(m):
        try:
            n = int(m.group(0))
            if abs(n) > 999999:
                return m.group(0)
            x = random.randint(1, 255)
            y = random.randint(1, 255)
            encoded = (n ^ x) + y
            return f'_ND({encoded},{x},{y})'
        except Exception:
            return m.group(0)
    return re.sub(r'(?<!\w)(\d+)(?!\w)', mutate, code)

def inject_junk(code: str, density: int = 3) -> str:
    lines = code.split("\n")
    result = []
    for line in lines:
        result.append(line)
        if random.random() < (density / 10):
            for _ in range(random.randint(1, density)):
                result.append(make_junk_block())
    return "\n".join(result)

def wrap_in_vm_exec(code: str) -> str:
    lines = code.split("\n")
    result = []
    for line in lines:
        m = re.search(r'(\w+)\s*([+\-*/%])\s*(\w+)', line)
        if m and random.random() < 0.3:
            op_map = {'+': '0x01', '-': '0x02', '*': '0x03', '/': '0x04', '%': '0x0A'}
            op_sym = m.group(2)
            if op_sym in op_map:
                a, b = m.group(1), m.group(3)
                op   = op_map[op_sym]
                new_expr = f'_VM_EXEC({op},{a},{b})'
                line = line[:m.start()] + new_expr + line[m.end():]
        result.append(line)
    return "\n".join(result)

def add_dead_code_paths(code: str) -> str:
    dead_blocks = [
        "if false then\n  local _dead = 'this never runs'\nend\n",
        "while false do\n  break\nend\n",
        f"if nil then\n  local {rand_name()} = 0\nend\n",
    ]
    lines = code.split("\n")
    result = []
    for line in lines:
        result.append(line)
        if random.random() < 0.08:
            result.append(random.choice(dead_blocks))
    return "\n".join(result)

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

@client.tree.command(name="say", description="Let bot send the message of what you say!")
@app_commands.describe(message="The message you want the bot to repeat.")
async def say(interaction: discord.Interaction, message: str):
    await interaction.response.send_message("Message sent!", ephemeral=True)
    await interaction.channel.send(message)

@client.tree.command(name="honeypot", description="Create a honeypot channel.")
@app_commands.describe(
    channel="Channel to set as honeypot.",
    message="Set the honeypot panel message.",
    punishment="Punishment type for anyone who sends a message (kick or ban).",
    allowed="Set an allowed user who can send messages in the honeypot channel."
)
@app_commands.choices(punishment=[
    app_commands.Choice(name="Kick", value="kick"),
    app_commands.Choice(name="Ban", value="ban"),
])
@app_commands.checks.has_permissions(manage_guild=True)
async def honeyp
ot(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    message: str = None,
    punishment: str = "kick",
    allowed: discord.Member = None
):
    panel_message = message

    client.honeypots[channel.id] = {
        "punishment": punishment,
        "allowed": allowed
    }
    client.honeypot_counts[channel.id] = 0

    title = "⚠️ DO NOT SEND MESSAGES IN THIS CHANNEL"
    description = panel_message if message else (
        "This channel is used to catch compromised accounts. "
        "Any messages sent here may result in a softkick."
    )
    label = "Kicks" if punishment == "kick" else "Bans"

    embed = discord.Embed(title=title, description=description, color=0xF4A732)
    embed.add_field(name=f"{label}", value="0", inline=True)

    try:
        sent = await channel.send(embed=embed)
        client.honeypot_panels[channel.id] = {"message_id": sent.id}
    except discord.Forbidden:
        await interaction.response.send_message(
            f"I don't have permission to send messages in {channel.mention}!",
            ephemeral=True
        )
        return

    allowed_text = f"{allowed.mention}" if allowed else "None"
    await interaction.response.send_message(
        f"## ✅ Honeypot Set!\n\n"
        f"- **Channel:** {channel.mention}\n"
        f"- **Punishment:** {punishment.capitalize()}\n"
        f"- **Allowed User:** {allowed_text}\n\n"
        f"Anyone who sends a message in that channel will be **{punishment}ned** automatically.",
        ephemeral=True
    )

@client.tree.command(name="obfuscate", description="Obfuscate a Lua script file.")
@app_commands.describe(file="Attach a .lua or .txt file that you want to obfuscate.", passes="Number of protection passes (1-5).")
async def obfuscate(interaction: discord.Interaction, file: discord.Attachment, passes: int = 3):
    if not (file.filename.endswith(".lua") or file.filename.endswith(".txt")):
        await interaction.response.send_message("Please upload a valid configuration matching either `.lua` or `.txt` expressions.", ephemeral=True)
        return
        
    if passes < 1 or passes > 5:
        await interaction.response.send_message("Passes parameter must be configured between 1 and 5.", ephemeral=True)
        return

    await interaction.response.defer()
    try:
        file_bytes = await file.read()
        source = file_bytes.decode("utf-8", errors="replace")
    except Exception:
        await interaction.followup.send("Failed to read the file content correctly.")
        return

    anti_hook = 'local _ENV_SNAP = {} local _SAFE_FUNCS = {"rawget","rawset","rawequal","rawlen","next","select","type","tostring","tonumber","pcall","xpcall","error","setmetatable","getmetatable","pairs","ipairs","unpack","math","string","table","bit32","os","game","workspace"} for _,_fn in ipairs(_SAFE_FUNCS) do _ENV_SNAP[_fn] = rawget(_G, _fn) end local function _HOOK_CHECK() for _,_fn in ipairs(_SAFE_FUNCS) do local _cur = rawget(_G, _fn) if _cur ~= nil and _cur ~= _ENV_SNAP[_fn] then local _dead = 0 while true do _dead = _dead + 1 end end end end '
    anti_debug = 'local _DBG_START = tick() local function _DEBUG_CHECK() local _t1 = tick() local _sum = 0 for _i = 1, 500 do _sum = _sum + _i end local _t2 = tick() if (_t2 - _t1) > 0.05 then local _inf = true while _inf do task.wait() end end if (tick() - _DBG_START) > 30 then local _inf = true while _inf do task.wait() end end end '
    self_healing = 'local _GUARD_TICK = tick() local _GUARD_CALLS = 0 local function _SELF_HEAL() _GUARD_CALLS = _GUARD_CALLS + 1 if _GUARD_CALLS % 25 == 0 then _HOOK_CHECK() _DEBUG_CHECK() end if (tick() - _GUARD_TICK) > 120 then _GUARD_TICK = tick() _HOOK_CHECK() end end '
    fast_vm = 'local _VM_OPS = {[0x01] = function(a,b) return a + b end, [0x02] = function(a,b) return a - b end, [0x03] = function(a,b) return a * b end, [0x04] = function(a,b) return a / b end, [0x0A] = function(a,b) return a % b end} local function _VM_EXEC(_op, _a, _b) local _fn = _VM_OPS[_op] if not _fn then return _a end return _fn(_a, _b) end '
    number_decoder = 'local _ND = function(_v, _x, _y) return bit32.bxor(_v, _x) - _y end '

    for _ in range(passes):
        xor_key = random.randint(1, 127)
        decoder_var = rand_name(8)
        str_decoder = f'local {decoder_var} = function(_t) local _r = {{}} for _i = 1, #_t do _r[_i] = string.char(bit32.bxor(_t[_i], {xor_key})) end return table.concat(_r) end '

        source = rename_variables(source)
        source = obfuscate_strings(source, xor_key, decoder_var)
        source = build_number_mutation_layer(source)
        source = inject_junk(source, density=3)
        source = add_dead_code_paths(source)
        source = wrap_in_vm_exec(source)

        tamper_len = len(source)
        tamper_guard = f'local function _TAMPER_CHECK(_src) if #_src ~= {tamper_len} then local _inf = true while _inf do task.wait() end end end '

        stamp = hashlib.md5(f"LuaLand{time.time()}".encode()).hexdigest()
        watermark = f'-- Obfuscated by LuaLand | ID: {stamp}\n'

        header = (
            watermark +
            anti_hook +
            anti_debug +
            tamper_guard +
            self_healing +
            fast_vm +
            str_decoder +
            number_decoder
        )
        source = header + source + "\n_SELF_HEAL()\n_HOOK_CHECK()\n_DEBUG_CHECK()\n"
        await asyncio.sleep(0)

    report_bytes = source.encode("utf-8")
    report_file = io.BytesIO(report_bytes)
    
    clean_filename = file.filename.replace(".lua", "_obf.lua") if file.filename.endswith(".lua") else file.filename.replace(".txt", "_obf.txt")
    discord_file = discord.File(fp=report_file, filename=clean_filename)

    response_text = f"## ╔══════════════════════════════════════════════════════╗\n##    Lua Land Protection Engine Complete!\n## ╚══════════════════════════════════════════════════════╝\n\n- **Security Configuration Passes:** {passes}\n- **Output Payload Size:** {len(source):,} bytes"

    try:
        await interaction.followup.send(content=response_text, file=discord_file)
    except Exception:
        pass

client.run(os.environ["TOKEN"])
