import discord
from discord import app_commands
from discord.ext import commands
import requests
import secrets
import string
import asyncio
import time

# =========================
# CONFIG
# =========================

TOKEN = "YOUR_DISCORD_BOT_TOKEN"
GUILD_ID = 123456789012345678
VERIFIED_ROLE_NAME = "Verified"
VERIFICATION_TIMEOUT = 300  # seconds (5 minutes)

# Brand colors / footer text — tweak to taste
BRAND_NAME = "VanishK Verification"
COLOR_MAIN = discord.Color.blurple()
COLOR_SUCCESS = discord.Color.green()
COLOR_FAIL = discord.Color.red()

# =========================
# BOT SETUP
# =========================

intents = discord.Intents.default()
intents.message_content = True  # required for prefix commands like ,verify

bot = commands.Bot(
    command_prefix=",",
    intents=intents
)

# discord_user_id -> {"roblox_id", "username", "display_name", "code", "expires_at"}
pending_verifications = {}


# =========================
# ROBLOX FUNCTIONS
# =========================

def get_roblox_user(username: str):
    url = "https://users.roblox.com/v1/usernames/users"

    payload = {
        "usernames": [username],
        "excludeBannedUsers": False
    }

    try:
        response = requests.post(url, json=payload, timeout=10)

        if response.status_code != 200:
            return None

        data = response.json()

        if not data.get("data"):
            return None

        user = data["data"][0]

        return {
            "id": user["id"],
            "name": user["name"],
            "display_name": user["displayName"]
        }

    except requests.RequestException:
        return None


def get_roblox_profile(user_id: int):
    url = f"https://users.roblox.com/v1/users/{user_id}"

    try:
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            return None

        return response.json()

    except requests.RequestException:
        return None


def get_roblox_avatar_url(user_id: int):
    """Headshot thumbnail URL for embeds."""
    url = "https://thumbnails.roblox.com/v1/users/avatar-headshot"
    params = {
        "userIds": str(user_id),
        "size": "150x150",
        "format": "Png",
        "isCircular": "false"
    }

    try:
        response = requests.get(url, params=params, timeout=10)

        if response.status_code != 200:
            return None

        data = response.json()

        if not data.get("data"):
            return None

        return data["data"][0].get("imageUrl")

    except requests.RequestException:
        return None


def generate_code():
    characters = string.ascii_uppercase + string.digits

    code = "".join(
        secrets.choice(characters)
        for _ in range(6)
    )

    return f"VANISH-{code}"


# =========================
# VERIFY / CANCEL VIEW
# =========================

class VerifyButton(discord.ui.View):
    """
    Persistent-friendly view. discord_user_id is embedded in custom_id so a
    fresh instance can be reconstructed if the bot restarts (see on_ready).
    """

    def __init__(self, discord_user_id: int, timeout=VERIFICATION_TIMEOUT):
        super().__init__(timeout=timeout)
        self.discord_user_id = discord_user_id

        # custom_id encodes the owner so buttons still work after a restart
        self.verify.custom_id = f"vanishk_verify:{discord_user_id}"
        self.cancel.custom_id = f"vanishk_cancel:{discord_user_id}"

    async def on_timeout(self):
        pending_verifications.pop(self.discord_user_id, None)
        for child in self.children:
            child.disabled = True

    def _record(self, user_id):
        record = pending_verifications.get(user_id)
        if record and record["expires_at"] < time.time():
            pending_verifications.pop(user_id, None)
            return None
        return record

    @discord.ui.button(
        label="Verify Roblox",
        style=discord.ButtonStyle.success,
        emoji="✅"
    )
    async def verify(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if interaction.user.id != self.discord_user_id:
            await interaction.response.send_message(
                "❌ This verification belongs to another Discord user.",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        record = self._record(interaction.user.id)

        if record is None:
            await interaction.followup.send(
                "⌛ This verification code has expired. Run `/vanishk` again.",
                ephemeral=True
            )
            self._disable_all()
            try:
                await interaction.message.edit(view=self)
            except discord.HTTPException:
                pass
            return

        profile = get_roblox_profile(record["roblox_id"])

        if profile is None:
            await interaction.followup.send(
                "⚠️ I couldn't reach Roblox right now. Try again in a moment.",
                ephemeral=True
            )
            return

        description = profile.get("description", "")

        if record["code"].lower() not in description.lower():
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Verification Failed",
                    description=(
                        f"I couldn't find `{record['code']}` in your Roblox About.\n\n"
                        "Double-check that you saved your profile after pasting "
                        "the code, then try again."
                    ),
                    color=COLOR_FAIL
                ),
                ephemeral=True
            )
            return

        # =========================
        # SUCCESS
        # =========================

        guild = interaction.guild

        if guild is None:
            await interaction.followup.send(
                "❌ This command can only be used inside a server.",
                ephemeral=True
            )
            return

        member = guild.get_member(interaction.user.id)

        if member is None:
            await interaction.followup.send(
                "❌ Couldn't find your Discord member information.",
                ephemeral=True
            )
            return

        role = discord.utils.get(guild.roles, name=VERIFIED_ROLE_NAME)

        if role is None:
            try:
                role = await guild.create_role(
                    name=VERIFIED_ROLE_NAME,
                    reason="Roblox verification role"
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    "❌ I don't have permission to create the Verified role.",
                    ephemeral=True
                )
                return

        try:
            await member.add_roles(role)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I can't give you the Verified role. "
                "Make sure my bot role is positioned above the Verified role.",
                ephemeral=True
            )
            return

        pending_verifications.pop(interaction.user.id, None)

        avatar_url = get_roblox_avatar_url(record["roblox_id"])

        embed = discord.Embed(
            title="✅ Verification Successful",
            description=f"Your Discord account is now linked to **{profile['name']}**.",
            color=COLOR_SUCCESS
        )
        embed.add_field(name="Roblox Username", value=f"`{profile['name']}`", inline=True)
        embed.add_field(name="Roblox ID", value=f"`{record['roblox_id']}`", inline=True)
        embed.add_field(name="Role Granted", value=role.mention, inline=True)

        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        embed.set_footer(text=BRAND_NAME)
        embed.timestamp = discord.utils.utcnow()

        await interaction.followup.send(embed=embed, ephemeral=True)

        self._disable_all(success=True)
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.danger,
        emoji="🗑️"
    )
    async def cancel(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        if interaction.user.id != self.discord_user_id:
            await interaction.response.send_message(
                "❌ This verification belongs to another Discord user.",
                ephemeral=True
            )
            return

        pending_verifications.pop(interaction.user.id, None)
        self._disable_all()

        await interaction.response.edit_message(
            content="🗑️ Verification cancelled.",
            embed=None,
            view=self
        )

    def _disable_all(self, success: bool = False):
        for child in self.children:
            child.disabled = True
            if success and isinstance(child, discord.ui.Button) and child.style == discord.ButtonStyle.success:
                child.label = "Verified ✅"


# =========================
# SHARED VERIFICATION STARTER
# =========================

async def start_verification(user_id: int, username: str):
    """
    Runs the whole 'start a verification' flow and returns
    (embed, view, error_message). error_message is None on success.
    """
    roblox_user = get_roblox_user(username)

    if roblox_user is None:
        return None, None, (
            "❌ I couldn't find that Roblox account. "
            "Make sure you entered the username correctly."
        )

    existing = pending_verifications.get(user_id)
    if existing and existing["expires_at"] > time.time():
        return None, None, (
            "⚠️ You already have a verification in progress. "
            "Finish it, wait for it to expire, or hit **Cancel** on the original message."
        )

    code = generate_code()
    expires_at = time.time() + VERIFICATION_TIMEOUT

    pending_verifications[user_id] = {
        "roblox_id": roblox_user["id"],
        "username": roblox_user["name"],
        "display_name": roblox_user["display_name"],
        "code": code,
        "expires_at": expires_at
    }

    avatar_url = get_roblox_avatar_url(roblox_user["id"])

    embed = discord.Embed(
        title="🔐 Roblox Verification",
        description=(
            "To prove you own this Roblox account, paste the code below into "
            "your **Roblox About**, save it, then click **Verify Roblox**."
        ),
        color=COLOR_MAIN
    )

    embed.add_field(
        name="👤 Roblox Account",
        value=(
            f"**Username:** `{roblox_user['name']}`\n"
            f"**Display Name:** `{roblox_user['display_name']}`"
        ),
        inline=False
    )

    embed.add_field(
        name="🔑 Verification Code",
        value=f"```{code}```",
        inline=False
    )

    embed.add_field(
        name="📋 Steps",
        value=(
            "**1.** Open your Roblox profile\n"
            "**2.** Tap **Edit About**\n"
            "**3.** Paste the code above (anywhere in the box)\n"
            "**4.** Save your profile\n"
            "**5.** Come back and click **Verify Roblox**"
        ),
        inline=False
    )

    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    embed.set_footer(text=f"{BRAND_NAME} • Code expires in 5 minutes")
    embed.timestamp = discord.utils.utcnow()

    view = VerifyButton(user_id)

    return embed, view, None


# =========================
# SLASH COMMAND: /vanishk
# =========================

@bot.tree.command(
    name="vanishk",
    description="Verify ownership of a Roblox account."
)
@app_commands.describe(username="Your Roblox username")
async def vanishk(interaction: discord.Interaction, username: str):
    await interaction.response.defer(ephemeral=True)

    embed, view, error = await start_verification(interaction.user.id, username)

    if error:
        await interaction.followup.send(error, ephemeral=True)
        return

    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


# =========================
# PREFIX COMMAND: ,verify
# =========================

@bot.command(name="verify")
async def verify_prefix(ctx: commands.Context, username: str = None):
    if username is None:
        await ctx.reply(
            "Usage: `,verify <roblox_username>`",
            mention_author=False
        )
        return

    embed, view, error = await start_verification(ctx.author.id, username)

    if error:
        await ctx.reply(error, mention_author=False)
        return

    # Prefix commands can't send ephemeral messages, so DM the user instead
    # to keep the code private, and confirm in-channel.
    try:
        await ctx.author.send(embed=embed, view=view)
        await ctx.reply(
            f"📬 {ctx.author.mention} check your DMs — I sent your verification code there.",
            mention_author=False
        )
    except discord.Forbidden:
        # DMs closed, fall back to sending in-channel
        await ctx.reply(embed=embed, view=view, mention_author=False)


# =========================
# BOT READY
# =========================

@bot.event
async def on_ready():
    try:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"Logged in as {bot.user} and synced slash commands.")
    except Exception as e:
        print(f"Command sync error: {e}")


# =========================
# RUN
# =========================

bot.run(TOKEN)
