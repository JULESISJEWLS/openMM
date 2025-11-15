import discord
from discord.ext import commands
import json
import os
import time
import difflib
from helper import *
import re
import requests
import random
from datetime import datetime
import asyncio

# global variables
guildSettings = {}
# Stores user join times to the queue vc's
voicePresence = {}
botOpen = False
matches = {}
penaltyData = {}

# global settings from file
try:
    with open("global_settings.json", "r") as f:
        global_settings = json.load(f)
        print("loaded global settings successfully")
except Exception as e:
    print("failed to load", e)
    global_settings = {}

token = global_settings["token"]
developerMode = global_settings.get("developerMode", False)
developerGuildId = global_settings.get("developerGuildId", None)
botName = global_settings.get("botName", "openMM")
madeByMessage = global_settings.get("madeByMessage", True)
madeByMessageContents = global_settings.get("madeByMessageContents", "Made by juleisjewls")
forbiddenChannels = global_settings.get("forbiddenChannels", ["general", "welcome", "announcements", "rules", "chat"])
botDocs = global_settings.get("botDocs", "https://github.com/JULESISJEWLS/openMM?tab=readme-ov-file")



def stripTag(text: str) -> str:
    """
    Removes a leading [tag] from a string and returns the remaining text.
    
    Args:
        text (str): The input string, e.g. "[tag] name"
    
    Returns:
        str: The text without the leading tag, e.g. "name"
    """
    match = re.match(r'^\[.*?\]\s*(.*)', text)
    if match:
        return match.group(1)
    return text

async def updateElo(member: discord.Member, guild: discord.Guild, stats: dict, updateNick: bool = False, update: bool = False) -> None:
    """
    Updates a member's statistics in the guild and optionally updates their nickname to show Elo.

    Args:
        member (discord.Member): The member whose stats are being updated.
        guild (discord.Guild): The guild where the member belongs.
        stats (dict): Dictionary of stats to update. Keys are stat names, values are integers.
        updateNick (bool, optional): If True, update the member's nickname to include their Elo. Defaults to False.
        update (bool, optional): If True, add provided values to existing stats. If False, overwrite existing stats. Defaults to False.

    Behavior:
        - Initializes guild and member entries in `guildStats` if they do not exist.
        - Only keys present in `stats` are affected.
        - If `update=True` and the key exists, the value is added to the current value.
        - If `update=False`, the value is set/overwritten.
        - Updates the member nickname with their Elo if `updateNick=True`.
        - Handles Discord permission errors gracefully when updating nicknames.

    Example:
        await updateElo(member, guild, {"elo": 25, "wins": 1}, updateNick=True, update=True)
    """
    global guildStats
    guildId = str(guild.id)
    if guildId not in guildStats:
        guildStats[guildId] = {}

    memberId = str(member.id)
    if memberId not in guildStats[guildId]:
        # initialize member stats if missing
        guildStats[guildId][memberId] = {"elo": 100, "wins": 0, "played": 0, "hosted": 0,}

    for key, value in stats.items():
        if update and key in guildStats[guildId][memberId]:
            # add to existing value
            guildStats[guildId][memberId][key] += value
        else:
            # set or overwrite value
            guildStats[guildId][memberId][key] = value

    if updateNick:
        try:
            newNick = f"[{guildStats[guildId][memberId].get('elo', 100)}] {stripTag(member.display_name)}"
            await member.edit(nick=newNick)
        except discord.Forbidden:
            # bot can't change nickname
            pass
        except Exception as e:
            log("EXCEPTION", f"Failed to update nickname for {member} in {guild.name}: {e}")


# load saved guild settings
def loadAllGuildSettings(baseDir="guilds", fileName = "settings.json") -> dict:
    allSettings = {}
    if not os.path.exists(baseDir):
        os.makedirs(baseDir)

    for folderName in os.listdir(baseDir):
        settingsPath = os.path.join(baseDir, folderName, fileName)
        if os.path.exists(settingsPath):
            try:
                with open(settingsPath, "r", encoding="utf-8") as f:
                    allSettings[str(folderName)] = json.load(f)
            except Exception as e:
                print(f"Failed to load {settingsPath}: {e}")
    return allSettings


# check if the bot is operational
async def canBotOperate(
    guild: discord.Guild,
    checkSetup: bool = True,
    explain: bool = False
):
    """
    Verify the bot is ready to operate in `guild`.

    Args:
        guild: the discord.Guild to check.
        checkSetup: if True, first verifies that required setup keys exist in guildSettings.
        explain: if True, return a tuple (ok: bool, missing: list[str]) where `missing` contains human-readable reasons.

    Returns:
        bool (or (bool, list[str]) if explain=True)
    """
    missing = []
    guildId = str(guild.id)
    settings = guildSettings.get(guildId, {})

    # setup completeness
    if checkSetup:
        requiredKeys = ["hostPanel", "hostRole", "matchesCatagory", "queueVoiceChannel", "blacklistRole", "hostShout"]
        if not all(key in settings and settings[key] is not None for key in requiredKeys):
            missing.append("Setup incomplete: one or more required settings missing.")
            if explain:
                return False, missing
            return False

    # bot member
    botMember = guild.me
    if not botMember:
        missing.append("I couldn't identify myself properly in this server. Please make sure I'm still in the server and have the right permissions.")
        if explain:
            return False, missing
        return False

    # check guild wide perms required for role/channel management
    requiredGuildPerms = [
        ("manage_roles", "Manage Roles"),
        ("manage_channels", "Manage Channels"),
        ("manage_nicknames", "Manage Nicknames"),
        ("view_channel", "View Channels"),
        ("send_messages", "Send Messages"),
        ("manage_messages", "Manage Messages"),
        ("move_members", "Move Members"),
    ]
    for attr, pretty in requiredGuildPerms:
        if not getattr(botMember.guild_permissions, attr, False):
            missing.append(f"Missing guild permission: {pretty}")

    # if we don't need channel checks early return but still may return missing guild perms
    if not checkSetup:
        ok = len(missing) == 0
        if explain:
            return ok, missing
        return ok

    # channel / role specific checks (only if setup exists)
    # hostPanel check
    hostPanel = guild.get_channel(settings.get("hostPanel"))
    if not isinstance(hostPanel, discord.TextChannel):
        missing.append("Configured hostPanel channel not found or not a text channel.")
    else:
        hostPerms = hostPanel.permissions_for(botMember)
        if not hostPerms.view_channel:
            missing.append(f"Bot cannot view host panel {hostPanel.mention}. (view_channel)")
        if not hostPerms.send_messages:
            missing.append(f"Bot cannot send messages in host panel {hostPanel.mention}. (send_messages)")
        if not hostPerms.manage_messages:
            missing.append(f"Bot cannot manage messages in host panel {hostPanel.mention}. (manage_messages)")

    # hostRole check
    hostRole = guild.get_role(settings.get("hostRole")) if settings.get("hostRole") else None
    if not isinstance(hostRole, discord.Role):
        missing.append("Configured hostRole not found.")
    else:
        # role must be assignable by bot: bot needs manage_roles and bot's top role higher than target role
        if not botMember.guild_permissions.manage_roles:
            missing.append("Bot lacks Manage Roles permission required to assign hostRole.")
        else:
            botTopPos = botMember.top_role.position
            if hostRole.position >= botTopPos:
                missing.append(f"Bot's top role is not higher than hostRole ({hostRole.name}); cannot assign it.")
            if hostRole.managed:
                missing.append(f"hostRole {hostRole.name} is managed by an integration and cannot be assigned manually.")

    # blacklistRole check (same as hostRole)
    blacklistRole = guild.get_role(settings.get("blacklistRole")) if settings.get("blacklistRole") else None
    if not isinstance(blacklistRole, discord.Role):
        missing.append("Configured blacklistRole not found.")
    else:
        if not botMember.guild_permissions.manage_roles:
            missing.append("Bot lacks Manage Roles permission required to assign blacklistRole.")
        else:
            botTopPos = botMember.top_role.position
            if blacklistRole.position >= botTopPos:
                missing.append(f"Bot's top role is not higher than blacklistRole ({blacklistRole.name}); cannot assign it.")
            if blacklistRole.managed:
                missing.append(f"blacklistRole {blacklistRole.name} is managed by an integration and cannot be assigned manually.")

    # matchesCategory check: ensure category exists and bot can manage channels (create/delete)
    matchesCategory = guild.get_channel(settings.get("matchesCatagory"))
    if not isinstance(matchesCategory, discord.CategoryChannel):
        missing.append("Configured matches category not found or not a category channel.")
    else:
        catPerms = matchesCategory.permissions_for(botMember)
        if not catPerms.manage_channels:
            missing.append(f"Bot cannot create/delete channels in category {matchesCategory.name}. (manage_channels)")

    # queueVoice check: ensure voice channel exists and bot can move/connect
    queueChannel = guild.get_channel(settings.get("queueVoiceChannel"))
    if not isinstance(queueChannel, discord.VoiceChannel):
        missing.append("Configured queue voice channel not found or not a voice channel.")
    else:
        queuePerms = queueChannel.permissions_for(botMember)
        if not getattr(botMember.guild_permissions, "move_members", False):
            missing.append("Bot lacks Move Members guild permission required to move members in voice channels.")

    # finalize
    ok = len(missing) == 0
    if explain:
        return ok, missing
    return ok

async def writeSettings(guild: discord.Guild) -> None:
    guildPath = f"guilds/{guild.id}"
    os.makedirs(guildPath, exist_ok=True)

    toWrite = guildSettings.get(str(guild.id), {})
    with open(f"{guildPath}/settings.json", "w") as f:
        f.write(json.dumps(toWrite, indent=4))

async def writeStats(guild: discord.Guild) -> None:
    toWrite = guildStats.get(str(guild.id), {})
    with open(f"guilds/{guild.id}/stats.json", "w") as f:
        f.write(json.dumps(toWrite, indent=4))

async def setupHostPanel(channel: discord.TextChannel):
    await channel.purge()
    embed = discord.Embed(
        title="Host Panel",
        description="Host Now!",
        color=discord.Color.orange()
    )
    view = HostPanelView()
    await channel.send(embed=embed, view=view)

async def startMatch(interaction: discord.Interaction, matchType: int, link: int) -> None:
    guild = interaction.guild

    # before all we makesure that it is safe to run this command
    out, explain = await canBotOperate(guild=interaction.guild, checkSetup=True, explain=True)
    if not out:
        explanationText = "\n".join(f"- {e}" for e in explain)
        embed = discord.Embed(
            title="Server Setup Required",
            description=f"I can't operate properly in this server yet:\n\n{explanationText}",
            color=0xF1C40F  # Yellow/orange for warning
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        guildSettings[str(interaction.guild_id)] = {}
        await writeSettings(guild=interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    canHost = False
    for role in interaction.user.roles:
        if role.id == guildSettings[str(guild.id)]["hostRole"]:
            canHost = True

    if not canHost:
        role = guild.get_role(guildSettings[str(guild.id)]["hostRole"])
        embed = discord.Embed(
            title="Missing permissions",
            description=f"You requre the {role.mention} role to host.",
            color=0xF1C40F  # Yellow/orange for warning
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    guildSet = guildSettings[str(guild.id)]

    queueChannelId = guildSet.get("queueVoiceChannel")
    queue = guild.get_channel(queueChannelId)

    if queue is None:
        try:
            queue = await guild.fetch_channel(queueChannelId)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            hostShout = None

    if queue is None:
        embed = discord.Embed(
            title="Error",
            description="The bot couldn't find the queue channel.",
            color=discord.Color.red()
        )
        if madeByMessage:
            embed.set_footer(text=madeByMessageContents)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    hostShoutChannelId = guildSet.get("hostShout")
    hostShout = guild.get_channel(hostShoutChannelId)

    if hostShout is None:
        try:
            hostShout = await guild.fetch_channel(hostShoutChannelId)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            hostShout = None

    if hostShout is None:
        embed = discord.Embed(
            title="Error",
            description="The bot couldn't find the host shout channel.",
            color=discord.Color.red()
        )
        if madeByMessage:
            embed.set_footer(text=madeByMessageContents)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    matchesCategoryId = guildSet.get("matchesCatagory") 
    matchesCategory = guild.get_channel(matchesCategoryId)

    if matchesCategory is None:
        try:
            matchesCategory = await guild.fetch_channel(matchesCategoryId)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            hostShout = None

    if hostShout is None:
        embed = discord.Embed(
            title="Error",
            description="The bot couldn't find the matchesCatagory catagory.",
            color=discord.Color.red()
        )

        if madeByMessage:
            embed.set_footer(text=madeByMessageContents)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    queueMembers = queue.members.copy() # snap shot of people in queue at that time

    for member in queueMembers: # remove bots
        if member.bot:
            queueMembers.pop(member)
    
    if len(queueMembers) < matchType * 2:
        embed = discord.Embed(
            title="Not enough people to start",
            description=f"there are {len(queueMembers)} in {queue.mention}.\nA {matchType}v{matchType} game requires unleast\n{matchType*2} players to start.",
            color=discord.Color.orange()
        )
        if madeByMessage:
            embed.set_footer(text=madeByMessageContents)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if interaction.user not in queueMembers:
        embed = discord.Embed(
            title="You are not in queue",
            description=f"To host a match it is required\nfor the host to be in the {queue.mention} voice channel.",
            color=discord.Color.orange()
        )
        if madeByMessage:
            embed.set_footer(text=madeByMessageContents)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    guildId = str(guild.id)
    voiceData = voicePresence.get(guildId, {})  # dict of memberId: joinTime
    queueMembers.sort(key=lambda m: voiceData.get(m.id, float('inf'))) # sort the list by time in queue
    queueMembers.remove(interaction.user)
    queueMembers.insert(0, interaction.user)

    queueMembers = queueMembers[:matchType*2]
    formattedMembers = {
    m.id: guildStats.get(guildId, {}).get(str(m.id), {}).get("elo", 100)
    for m in queueMembers
    }

    matchId = generateShortId(matches)
    teams = getTeams(formattedMembers)
    preGameEloCalc = calculatePreGameElo(teams)
    print(preGameEloCalc)
    redStr = []
    blueStr = []
    for m in queueMembers:
        if m == interaction.user:
            await updateElo(
                member=m,
                guild=interaction.guild,
                stats={"hosted": 1},
                updateNick=False,
                update=True   
            )
        if m.id in teams["redTeam"]:
            team, r, g, b = "🔴Red", 255, 0, 0
            redStr.append(m.mention)
        else:
            team, r, g, b = "🔵Blue", 0, 0, 255
            blueStr.append(m.mention)

        embed = discord.Embed(
            title="Match Started!",
            description=f"Click the link above to join you are on {team} team.",
            color=discord.Color.from_rgb(r, g ,b)
        )
        if madeByMessage:
            embed.set_footer(text=madeByMessageContents)
        try:
            await m.send(content=link, embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            embed = discord.Embed(
                title="Failed to DM",
                description=f"I failed to dm {m.mention} please send them the link manually.\nreason: {e}",
                color=discord.Color.from_rgb(255, 255 ,255)
            )
            if madeByMessage:
                embed.set_footer(text=madeByMessageContents)
            await interaction.followup.send(embed=embed, ephemeral=True)

    embed = discord.Embed(
    title=f"`{interaction.user.display_name}` has started a {matchType}v{matchType} match!",
    description="Check your DMs for the link!",
    color=discord.Color.blue()
    )


    # Add team fields
    embed.add_field(
        name="Red Team",
        value="\n".join(redStr) if redStr else "No players",
        inline=True
    )
    embed.add_field(
        name="Blue Team",
        value="\n".join(blueStr) if blueStr else "No players",
        inline=True
    )
    if madeByMessage:
        embed.set_footer(text=madeByMessageContents)

    await hostShout.send(embed=embed, view=matchPanelView(host=interaction.user, matchId=matchId))


    botRole = guild.me.top_role  # bot's top role

    overwritesRed = {
        guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True),
        botRole: discord.PermissionOverwrite(connect=True, move_members=True)
    }
    overwritesBlue = {
        guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True),
        botRole: discord.PermissionOverwrite(connect=True, move_members=True)
    }

    # Allow only team members to connect
    for memberId in teams["redTeam"]:
        member = guild.get_member(memberId)
        if member:
            overwritesRed[member] = discord.PermissionOverwrite(connect=True)

    for memberId in teams["blueTeam"]:
        member = guild.get_member(memberId)
        if member:
            overwritesBlue[member] = discord.PermissionOverwrite(connect=True)

    # Create the voice channels
    redVc = await guild.create_voice_channel(
        name=f"🔴 Red Team {matchId}",
        category=matchesCategory,
        overwrites=overwritesRed
    )

    blueVc = await guild.create_voice_channel(
        name=f"🔵 Blue Team {matchId}",
        category=matchesCategory,
        overwrites=overwritesBlue
    )
    redTeam = []
    blueTeam = []
    for m in queueMembers:
        try:
            if m.id in teams["redTeam"]:
                redTeam.append(m)
                await m.move_to(redVc)
            if m.id in teams["blueTeam"]:
                blueTeam.append(m)
                await m.move_to(blueVc)
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"Failed to move {m}: {e}")
            embed = discord.Embed(
                title="Failed to Move",
                description=f"I failed to Move {m.mention} please move them manually.\nreason: {e}",
                color=discord.Color.from_rgb(255, 255 ,255)
            )
            if madeByMessage:
                embed.set_footer(text=madeByMessageContents)
            await interaction.followup.send(embed=embed)
    extendedList = redStr + blueStr


    matches[matchId] = {
        "preGameEloCalc": preGameEloCalc,
        "redTeam": redTeam,
        "blueTeam": blueTeam,
        "who won": None,
        "host": interaction.user,
        "redVC": redVc,
        "blueVC": blueVc,
        "redMention": "".join(redStr),
        "blueMention": "".join(blueStr),
        "allMention": "".join(extendedList),
        "queue": queue,
        "link": link
    }

    await interaction.followup.send("success", ephemeral=True)

    await writeStats(interaction.guild)

async def cancelMatch(interaction: discord.Interaction, matchId: str, reason: str) -> None:
    if matchId not in matches or matches[matchId]["who won"] is not None:
        embed = discord.Embed(
            title="Match has already been ended/cancelled",
            description="...",
            color=discord.Color.from_rgb(255, 165, 0)
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    match = matches.pop(matchId)

    await interaction.response.defer(thinking=True)

    redVc: discord.VoiceChannel = match["redVC"]
    blueVc: discord.VoiceChannel = match["blueVC"]
    try:
        await redVc.delete()
    
    except (discord.Forbidden, discord.HTTPException, discord.NotFound) as e:
        embed = discord.Embed(
            title="Failed to Delete",
            description=f"I failed to delete the red team vc.\nreason: {e}",
            color=discord.Color.from_rgb(255, 255 ,255)
        )
        if madeByMessage:
            embed.set_footer(text=madeByMessageContents)
        await interaction.followup.send(embed=embed, ephemeral=True)
    try:
        await blueVc.delete()

    except (discord.Forbidden, discord.HTTPException, discord.NotFound):
        embed = discord.Embed(
            title="Failed to Delete",
            description=f"I failed to delete the blue team vc.\nreason: {e}",
            color=discord.Color.from_rgb(255, 255 ,255)
        )
        if madeByMessage:
            embed.set_footer(text=madeByMessageContents)
        await interaction.followup.send(embed=embed, ephemeral=True)

    embed = discord.Embed(
        title="Match Cancelled",
        description=f"Match Cancelled by `{interaction.user.display_name}`\nReason: `{reason}`",
        color=discord.Color.from_rgb(255, 165, 0)
    )
    embed.set_footer(text=madeByMessageContents if madeByMessage else None)
    await interaction.followup.send(content=match["allMention"], embed=embed)

async def endMatch(interaction: discord.Interaction, matchId: str, winner: int):
    if matchId not in matches or matches[matchId]["who won"] is not None:
        embed = discord.Embed(
            title="Match has already been ended/cancelled",
            description="...",
            color=discord.Color.from_rgb(255, 165, 0)
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    cMatch = matches[matchId] # current match
    redTeam: list[discord.Member] = cMatch["redTeam"]
    blueTeam: list[discord.Member] = cMatch["blueTeam"]
    cMatch["who won"] = winner
    desc = ""
    if not winner:
        redTeamResStr, RSymbol, rWin = "won", "+", 1
        blueTeamResStr, BSymbol, bWin = "loss", "", 0
    else:
        redTeamResStr, RSymbol, rWin = "loss", "", 0
        blueTeamResStr, BSymbol, bWin = "won", "+", 1

    for m in redTeam:
        await updateElo(
            member=m,
            guild=m.guild,
            stats={
                "elo": cMatch["preGameEloCalc"]["redTeam"][m.id][redTeamResStr],
                "played": 1,
                "wins": rWin
            },
            updateNick=True,
            update=True
        )
        desc += f"\n`{m.display_name}` **{RSymbol}{cMatch['preGameEloCalc']['redTeam'][m.id][redTeamResStr]}**"

    for m in blueTeam:
        await updateElo(
            member=m,
            guild=m.guild,
            stats={
                "elo": cMatch["preGameEloCalc"]["blueTeam"][m.id][blueTeamResStr],
                "played": 1,
                "wins": bWin
            },
            updateNick=True,
            update=True
        )
        desc += f"\n`{m.display_name}` **{BSymbol}{cMatch['preGameEloCalc']['blueTeam'][m.id][blueTeamResStr]}**"

    redVc: discord.VoiceChannel = cMatch["redVC"]
    blueVc: discord.VoiceChannel = cMatch["blueVC"]

    try:
        await redVc.delete()
    
    except (discord.Forbidden, discord.HTTPException, discord.NotFound) as e:
        embed = discord.Embed(
            title="Failed to Delete",
            description=f"I failed to delete the red team vc.\nreason: {e}",
            color=discord.Color.from_rgb(255, 255 ,255)
        )
        if madeByMessage:
            embed.set_footer(text=madeByMessageContents)
        await interaction.followup.send(embed=embed, ephemeral=True)
    try:
        await blueVc.delete()

    except (discord.Forbidden, discord.HTTPException, discord.NotFound):
        embed = discord.Embed(
            title="Failed to Delete",
            description=f"I failed to delete the blue team vc.\nreason: {e}",
            color=discord.Color.from_rgb(255, 255 ,255)
        )
        if madeByMessage:
            embed.set_footer(text=madeByMessageContents)
        await interaction.followup.send(embed=embed, ephemeral=True)

    embed = discord.Embed(
        title="Match Ended",
        description=f"Match Ended by `{interaction.user.display_name}`\nWinning team: {'🔴 Red' if winner == 0 else '🔵 Blue'}",
        color=discord.Color.from_rgb(0, 255, 0)
    )
    embed.set_footer(text=madeByMessageContents if madeByMessage else None)


    statsEmbed = discord.Embed(
        title="Stats",
        description=desc,
        color=discord.Color.from_rgb(0, 255, 0)
    )
    embed.set_footer(text=madeByMessageContents if madeByMessage else None)

    await interaction.followup.send(content=cMatch["allMention"], embed=embed, view=EndMatchView(embed=statsEmbed, matchId=matchId, host=interaction.user))

    await writeStats(interaction.guild)

async def swapWinners(interaction: discord.Interaction, matchId: str):
    if matchId not in matches:
        embed = discord.Embed(
            title="Match Not Found",
            description="This match has already been cancelled or doesn't exist.",
            color=discord.Color.from_rgb(255, 165, 0)
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    cMatch = matches[matchId]

    if cMatch["who won"] is None:
        embed = discord.Embed(
            title="Match Still Active",
            description="You can't swap winners for a match that hasn't been ended yet.",
            color=discord.Color.from_rgb(255, 165, 0)
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer(thinking=True)

    # swap winner
    newWinner = 0 if cMatch["who won"] == 1 else 1
    cMatch["who won"] = newWinner

    redTeam: list[discord.Member] = cMatch["redTeam"]
    blueTeam: list[discord.Member] = cMatch["blueTeam"]

    desc = ""
    if not newWinner:
        redTeamResStr, RSymbol, rWin = "won", "+", 1
        blueTeamResStr, BSymbol, bWin = "loss", "", -1
    else:
        redTeamResStr, RSymbol, rWin = "loss", "", -1
        blueTeamResStr, BSymbol, bWin = "won", "+", 1

    # apply reversed ELO results
    for m in redTeam:
        await updateElo(
            member=m,
            guild=m.guild,
            stats={
                "elo": cMatch["preGameEloCalc"]["redTeam"][m.id][redTeamResStr] * 2,
                "played": 0,  # do not double-count matches
                "wins": rWin
            },
            updateNick=True,
            update=True
        )
        desc += f"\n`{m.display_name}` **{RSymbol}{cMatch['preGameEloCalc']['redTeam'][m.id][redTeamResStr]}**"

    for m in blueTeam:
        await updateElo(
            member=m,
            guild=m.guild,
            stats={
                "elo": cMatch["preGameEloCalc"]["blueTeam"][m.id][blueTeamResStr] * 2,
                "played": 0,
                "wins": bWin
            },
            updateNick=True,
            update=True
        )
        desc += f"\n`{m.display_name}` **{BSymbol}{cMatch['preGameEloCalc']['blueTeam'][m.id][blueTeamResStr]}**"

    embed = discord.Embed(
        title="Winner Swapped",
        description=f"Winners have been swapped by `{interaction.user.display_name}`.\nNew Winning Team: {'🔴 Red' if newWinner == 0 else '🔵 Blue'}",
        color=discord.Color.from_rgb(0, 255, 0)
    )
    embed.set_footer(text=madeByMessageContents if madeByMessage else None)

    statsEmbed = discord.Embed(
        title="Updated Stats",
        description=desc,
        color=discord.Color.from_rgb(0, 255, 0)
    )
    statsEmbed.set_footer(text=madeByMessageContents if madeByMessage else None)

    await interaction.followup.send(
        content=cMatch["allMention"],
        embed=embed,
        view=EndMatchView(embed=statsEmbed, matchId=matchId, host=interaction.user)
    )

    await writeStats(interaction.guild)

def parse_duration(duration_str: str) -> int:
    """
    Parse a string like '1d 2h 30m' into total seconds.
    Supports d (days), h (hours), m (minutes), s (seconds).
    """
    total_seconds = 0
    pattern = r"(\d+)([dhms])"
    for amount, unit in re.findall(pattern, duration_str.lower()):
        amount = int(amount)
        if unit == "d":
            total_seconds += amount * 86400
        elif unit == "h":
            total_seconds += amount * 3600
        elif unit == "m":
            total_seconds += amount * 60
    return total_seconds

# views/modals
class RevertMatchModal(discord.ui.Modal): # modal used to cancel a match called by matchPanelView
    def __init__(self, matchId: str, user: discord.Member):
        random.seed(time.time())
        randomWord = random.choice([
            "M14", "Fate AR2", "banana", "openMM",
            "VRS=buns", "HTC=buns", "AK-74", "ASS VAL",
            "MiniMaxiBoob", "pridefull is buns"
        ])

        super().__init__(title="Swap Winners")

        self.matchId = matchId
        self.user = user
        self.randomWord = randomWord


        self.confirm = discord.ui.TextInput(
            label="Type the word below to confirm revertion.",
            placeholder=randomWord,
            required=True,
            max_length=50
        )
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip().lower() != self.randomWord.lower():
            embed = discord.Embed(
                title="Confirmation Failed",
                description=f"You typed in `{self.confirm.value.lower()}` we needed `{self.randomWord.lower()}`.",
                color=discord.Color.from_rgb(255, 165, 0)
            )
            embed.set_footer(text=madeByMessageContents if madeByMessage else None)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        # Example cancellation logic
        await swapWinners(interaction=interaction, matchId=self.matchId)

class EndMatchView(discord.ui.View):
    def __init__(self, embed: discord.Embed, matchId: str, host: discord.Member):
        super().__init__(timeout=None)
        self.embed = embed
        self.matchId = matchId
        self.host = host
    @discord.ui.button(label="See Elo Change", style=discord.ButtonStyle.primary, emoji="🏆")
    async def seeStats(self, interaction: discord.Interaction, button: discord.ui.Button):
        out, explain = await canBotOperate(guild=interaction.guild, checkSetup=True, explain=True)
        if not out:
            explanationText = "\n".join(f"- {e}" for e in explain)
            await interaction.response.send_message(
                f"I can't operate properly in this server yet:\n\n{explanationText}",
                ephemeral=True
            )
            return

        await interaction.response.send_message(embed=self.embed, ephemeral=True)

    @discord.ui.button(label="Swap Winners", style=discord.ButtonStyle.secondary, emoji="🔁")
    async def swapWin(self, interaction: discord.Interaction, button: discord.ui.Button):
        out, explain = await canBotOperate(guild=interaction.guild, checkSetup=True, explain=True)
        if not out:
            explanationText = "\n".join(f"- {e}" for e in explain)
            await interaction.response.send_message(
                f"I can't operate properly in this server yet:\n\n{explanationText}",
                ephemeral=True
            )
            return
        perms = interaction.user.guild_permissions

        if perms.administrator or perms.manage_guild or perms.moderate_members or interaction.user == self.host:
            await interaction.response.send_modal(RevertMatchModal(matchId=self.matchId, user=interaction.user))
        else:
            embed = discord.Embed(
                title="Invalid permissions",
                description=f"You must have `administrator`, `manage_guild` or `moderate_members` permissions or be the host that started the game.",
                color=discord.Color.from_rgb(255, 165, 0)
            )
            embed.set_footer(text=madeByMessageContents if madeByMessage else None)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

class HostPanelView(discord.ui.View): # view used for the host panel calls StartMatchModal
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Start Match", style=discord.ButtonStyle.primary)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        out, explain = await canBotOperate(guild=interaction.guild, checkSetup=True, explain=True)
        if not out:
            explanationText = "\n".join(f"- {e}" for e in explain)
            await interaction.response.send_message(
                f"I can't operate properly in this server yet:\n\n{explanationText}",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(StartMatchModal())

    @discord.ui.button(label="Help", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("help text", ephemeral=True)

class matchPanelView(discord.ui.View): # view sent to the match panel used to end matches and cancel them
    def __init__(self, host: discord.Member, matchId: str):
        super().__init__(timeout=None)
        self.host = host
        self.matchId = matchId
    @discord.ui.button(label="Confirm Red win", style=discord.ButtonStyle.red, emoji="🟥")
    async def endMatchRedTeam(self, interaction: discord.Interaction, button: discord.ui.Button):
        perms = interaction.user.guild_permissions

        if perms.administrator or perms.manage_guild or perms.moderate_members or interaction.user == self.host:
            await endMatch(interaction=interaction, matchId=self.matchId, winner=0)
        else:
            embed = discord.Embed(
                title="Invalid permissions",
                description=f"You must have `administrator`, `manage_guild` or `moderate_members` permissions or be the host that started the game.",
                color=discord.Color.from_rgb(255, 165, 0)
            )
            embed.set_footer(text=madeByMessageContents if madeByMessage else None)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    @discord.ui.button(label="Confirm Blue win", style=discord.ButtonStyle.blurple, emoji="🟦")
    async def endMatchBlueTeam(self, interaction: discord.Interaction, button: discord.ui.Button):
        perms = interaction.user.guild_permissions

        if perms.administrator or perms.manage_guild or perms.moderate_members or interaction.user == self.host:
            await endMatch(interaction=interaction, matchId=self.matchId, winner=1)
        else:
            embed = discord.Embed(
                title="Invalid permissions",
                description=f"You must have `administrator`, `manage_guild` or `moderate_members` permissions or be the host that started the game.",
                color=discord.Color.from_rgb(255, 165, 0)
            )
            embed.set_footer(text=madeByMessageContents if madeByMessage else None)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    @discord.ui.button(label="Cancel Match", style=discord.ButtonStyle.danger, emoji="⚠️")
    async def cancelMatch(self, interaction: discord.Interaction, button: discord.ui.Button):
        perms = interaction.user.guild_permissions

        if perms.administrator or perms.manage_guild or perms.moderate_members or interaction.user == self.host:
            await interaction.response.send_modal(CancelMatchModal(self.matchId, self.host))
        else:
            embed = discord.Embed(
                title="Invalid permissions",
                description=f"You must have `administrator`, `manage_guild` or `moderate_members` permissions or be the host that started the game.",
                color=discord.Color.from_rgb(255, 165, 0)
            )
            embed.set_footer(text=madeByMessageContents if madeByMessage else None)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    @discord.ui.button(label="Replace Player", style=discord.ButtonStyle.grey, emoji="🥸")
    async def replacePlayer(self, interaction: discord.Interaction, button: discord.ui.Button):
        perms = interaction.user.guild_permissions
        match = matches.get(self.matchId)

        # Check if match exists
        if not match:
            await interaction.response.send_message("The match already ended!", ephemeral=True)
            return

        # Check permissions
        if not (perms.administrator or perms.manage_guild or perms.moderate_members or interaction.user == self.host):
            embed = discord.Embed(
                title="Invalid permissions",
                description="You must have `administrator`, `manage_guild`, `moderate_members` permissions, or be the host of the match.",
                color=discord.Color.orange()
            )
            embed.set_footer(text=madeByMessageContents if madeByMessage else None)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Build list of valid replacements (exclude match members)
        match_members = match["redTeam"] + match["blueTeam"]
        queue_members = [m for m in match["queue"].members if m not in match_members]

        if not queue_members:
            await interaction.response.send_message("there are no available replacements in the queue.", ephemeral=True)
            return

        # Show ReplacePlayerView with only valid replacements
        all_match_members = match_members  # still used as selectable "from" players
        view = ReplacePlayerView(all_match_members, queue_members, self.matchId)
        await interaction.response.send_message("Select a player to replace:", ephemeral=True, view=view)

class CancelMatchModal(discord.ui.Modal): # modal used to cancel a match called by matchPanelView
    def __init__(self, matchId: str, user: discord.Member):
        random.seed(time.time())
        randomWord = random.choice([
            "M14", "Fate AR2", "banana", "openMM",
            "VRS=buns", "HTC=buns", "AK-74", "ASS VAL",
            "MiniMaxiBoob", "pridefull is buns"
        ])

        super().__init__(title="Cancel Match")

        self.matchId = matchId
        self.user = user
        self.randomWord = randomWord

        self.reason = discord.ui.TextInput(
            label="Reason for cancelling the match",
            placeholder="Enter a brief reason...",
            required=True,
            max_length=50
        )
        self.add_item(self.reason)

        self.confirm = discord.ui.TextInput(
            label="Type the word below to confirm cancellation",
            placeholder=randomWord,
            required=True,
            max_length=50
        )
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction):
        if self.confirm.value.strip().lower() != self.randomWord.lower():
            embed = discord.Embed(
                title="Confirmation Failed",
                description=f"You typed in `{self.confirm.value.lower()}` we needed `{self.randomWord.lower()}`.",
                color=discord.Color.from_rgb(255, 165, 0)
            )
            embed.set_footer(text=madeByMessageContents if madeByMessage else None)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        # Example cancellation logic
        await cancelMatch(interaction, matchId=self.matchId, reason=self.reason.value)

class StartMatchModal(discord.ui.Modal, title="Submit Match Info"): # modal used to start the match called bby hostpanelcview
    matchType = discord.ui.TextInput(
        label="Match Type",
        placeholder="eg 3v3, 4v4, 5v5",
        required=True,
        max_length=3
    )

    link = discord.ui.TextInput(
        label="Link",
        placeholder="priv server link/a match code etc",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        matchType = self.matchType.value.lower()

        if matchType not in ["1v1","3v3", "4v4", "5v5"]:
            embed = discord.Embed(
                title="Invalid match type",
                description="match type must be either `3v3`, `4v4` or `5v5`",
                color=discord.Color.from_rgb(255, 165, 0)
            )
            embed.set_footer(text=madeByMessageContents if madeByMessage else None)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        await startMatch(interaction=interaction, matchType=int(matchType[0]), link=self.link.value)

class ReplacePlayerView(discord.ui.View):
    def __init__(self, teamA: list[discord.Member], teamB: list[discord.Member], matchId: str):
        super().__init__(timeout=60)
        self.teamA = teamA  # players currently in the match
        self.teamB = teamB  # replacements from queue
        self.matchId = matchId
        self.selectedFromA = None
        self.selectedFromB = None

        self.add_item(self.create_select(teamA, "player_to_replace", "Select player to replace"))
        self.add_item(self.create_select(teamB, "replacement", "Select replacement"))

    def create_select(self, team: list[discord.Member], customId: str, placeholder: str):
        options = [discord.SelectOption(label=m.display_name, value=str(m.id)) for m in team]
        select = discord.ui.Select(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options,
            custom_id=customId
        )

        async def select_callback(interaction: discord.Interaction):
            if customId == "player_to_replace":
                self.selectedFromA = int(select.values[0])
            else:
                self.selectedFromB = int(select.values[0])
            await interaction.response.defer()

        select.callback = select_callback
        return select

    @discord.ui.button(label="Replace Player!", style=discord.ButtonStyle.primary)
    async def replace(self, interaction: discord.Interaction, button: discord.ui.Button):
        match = matches.get(self.matchId)
        if not match:
            await interaction.response.send_message("The match has already ended.", ephemeral=True)
            return
        if match.get("is won") is not None:
            await interaction.response.send_message("The match has already ended.", ephemeral=True)
            return
        if not self.selectedFromA or not self.selectedFromB:
            await interaction.response.send_message("Please select both a player to replace and a replacement.", ephemeral=True)
            return

        guild = interaction.guild
        memberToReplace = guild.get_member(self.selectedFromA)
        replacement = guild.get_member(self.selectedFromB)
        if not memberToReplace or not replacement:
            await interaction.response.send_message("Could not find one of the selected members.", ephemeral=True)
            return

        # Determine team and VC
        if memberToReplace in match["redTeam"]:
            teamList = match["redTeam"]
            vc = match["redVC"]
            color, r, g, b = "🔴 Red", 255, 0, 0
        elif memberToReplace in match["blueTeam"]:
            teamList = match["blueTeam"]
            vc = match["blueVC"]
            color, r, g, b = "🔵 Blue", 0, 0, 255
        else:
            await interaction.response.send_message("The player to replace is not in the match.", ephemeral=True)
            return

        # Update team list
        teamList.remove(memberToReplace)
        teamList.append(replacement)

        # Update VC overwrites
        overwrites = vc.overwrites
        overwrites[memberToReplace] = discord.PermissionOverwrite(connect=False)
        overwrites[replacement] = discord.PermissionOverwrite(connect=True)

        try:
            await vc.edit(overwrites=overwrites)
            await replacement.move_to(vc)
            await memberToReplace.move_to(match["queue"])
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send("Failed to move members or update VC permissions. Check bot permissions.", ephemeral=True)
            return

        # Update match dict
        if memberToReplace in match["redTeam"]:
            match["redTeam"] = teamList
        else:
            match["blueTeam"] = teamList

        # Remove replacement from queue
        if replacement in match["queue"].members:
            match["queue"].members.remove(replacement)

        # Recalculate pre-game Elo
        teams = getTeams({
            m.id: guildStats.get(str(m.guild.id), {}).get(str(m.id), {}).get("elo", 100)
            for m in match["redTeam"] + match["blueTeam"]
        })
        match["preGameEloCalc"] = calculatePreGameElo(teams)
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(
            f"Replaced **{memberToReplace.display_name}** with **{replacement.display_name}**.",
            ephemeral=True
        )

        embed = discord.Embed(
            title="Match Started!",
            description=f"Click the link above to join. you are on {color} Team.",
            color=discord.Color.from_rgb(r, g, b)
        )
        if madeByMessage:
            embed.set_footer(text=madeByMessageContents)

        try:
            await replacement.send(content=match['link'], embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            embed = discord.Embed(
                title="Failed to DM",
                description=f"I failed to dm {replacement.mention} please send them the link manually.\nreason: {e}",
                color=discord.Color.from_rgb(255, 255 ,255)
            )
            if madeByMessage:
                embed.set_footer(text=madeByMessageContents)
            await interaction.followup.send(embed=embed, ephemeral=True)


# bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

guildSettings = loadAllGuildSettings()
guildStats = loadAllGuildSettings(fileName="stats.json")


# bot events
@bot.event
async def on_ready():
    global botOpen

    # Set bot status
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Game("OpenMM ALPHA, new features coming soon!")
    )

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    # sync slash commands
    await syncCommands()

    # restore host panels if they exist
    for guild in bot.guilds:
        channelId = guildSettings.get(str(guild.id), {}).get("hostPanel")
        if channelId:
            channel = guild.get_channel(channelId)
            if channel:
                try:
                    log(typ="SETUP", text=f"Setting/restoring host panel in {guild.name}({guild.id}).")
                    await setupHostPanel(channel=channel)
                    log(typ="SUCCESS", text=f"Succesfully setup/restored host panel in {guild.name}({guild.id}).")
                except Exception as e:
                    log(typ="ERROR", text=f"failed to setup/restore host panel in {guild.name}({guild.id}).")
                    print(f"Failed to restore host panel in {guild.id}: {e}")

    botOpen = True

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    guild = member.guild
    guildId = str(guild.id)
    memberId = member.id

    queueChannelId = guildSettings.get(guildId, {}).get("queueVoiceChannel", None)
    blacklistRoleId = guildSettings.get(guildId, {}).get("blacklistRole", None)
    if not queueChannelId:
        return  # skip if they aren't in the queue VC

    role = guild.get_role(blacklistRoleId)

    # Check if member has an active penalty
    active_penalty = penaltyData.get(guildId, {}).get(memberId, [])
    has_active_penalty = any(
        (datetime.utcnow() - start).total_seconds() < duration for start, duration in active_penalty
    )

    # Remove blacklist role if they have it but no active penalty
    if role in member.roles and not has_active_penalty:
        try:
            await member.remove_roles(role, reason="Penalty expired but role still present")
        except discord.Forbidden:
            pass

    # helper function to kick blacklisted users
    async def kick_if_blacklisted():
        if blacklistRoleId and any(role.id == int(blacklistRoleId) for role in member.roles):
            try:
                await member.move_to(None)  # disconnect from VC
                return True
            except Exception as e:
                pass
        return False

    # user joins the queue VC
    if after.channel and after.channel.id == queueChannelId and (before.channel is None or before.channel.id != queueChannelId):
        if await kick_if_blacklisted():
            return  # don't track if kicked
        guildData = voicePresence.setdefault(guildId, {})
        guildData[memberId] = time.time()

    # user leaves the VC
    elif before.channel and before.channel.id == queueChannelId and (after.channel is None or after.channel.id != queueChannelId):
        if guildId in voicePresence and memberId in voicePresence[guildId]:
            voicePresence[guildId].pop(memberId)

    # user switches VC out of queue VC
    elif before.channel and after.channel and before.channel.id == queueChannelId and after.channel.id != queueChannelId:
        if guildId in voicePresence and memberId in voicePresence[guildId]:
            voicePresence[guildId].pop(memberId)

    # user switches into the queue VC
    elif before.channel and after.channel and before.channel.id != queueChannelId and after.channel.id == queueChannelId:
        if await kick_if_blacklisted():
            return  # don't track if kicked
        guildData = voicePresence.setdefault(guildId, {})
        guildData[memberId] = time.time()

# command sync
async def syncCommands():
    try:
        start = time.time()

        if developerMode and developerGuildId:
            # sync only to developer guild for fast testing
            log("info", "Started sycning command(s) in developer mode")
            guild = discord.Object(id=developerGuildId)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            log("setup", f"finished syncing {len(synced)} command(s) in {round(time.time()-start,2)}s")

        else:
            # sync globally for all guilds
            log("info", "Started sycning command(s) globally")
            synced = await bot.tree.sync()
            log("setup", f"finished syncing {len(synced)} command(s) in {round(time.time()-start,2)}s")

    except Exception as error:
        print(f"failed to sync commands: {error}")

# bot commands
@bot.tree.command(name="setup", description="setup up the bot for your server")
async def setup(interaction: discord.Interaction, blacklist_role: discord.Role, host_role: discord.Role, queue: discord.VoiceChannel, host_panel: discord.TextChannel, matches_catagory: discord.CategoryChannel, host_shout: discord.TextChannel) -> None:
    if not interaction.user.guild_permissions.administrator:
        embed = discord.Embed(
            title="Missing Permissions",
            description="you need administrator to use this command.",
            color=discord.Color.from_rgb(255, 0, 0)
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    if blacklist_role == host_role:
        embed = discord.Embed(
            title="Role conflict",
            description="'blacklist_role' and 'host_role' can not be equal.",
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # provent people from trying to use a
    channelName = host_panel.name.lower()
    for forbidden in forbiddenChannels:
        ratio = difflib.SequenceMatcher(None, channelName, forbidden).ratio()
        if ratio >= 0.8:
            embed = discord.Embed(
                title="Host Panel Conflict",
                description=(
                    f"The channel {host_panel.mention} is too similar to the protected name `{forbidden}`.\n\n"
                    f"Please note that using this channel as the host panel will purge all its messages.\n"
                    f"Consider creating a new channel or renaming {host_panel.mention} to avoid conflicts.\n"
                    f"I'd highly recommend reading the documentation/guide on the bot [here]({botDocs})."
                ),
                color=discord.Color.from_rgb(255, 0, 0)
            )
            embed.set_footer(text=madeByMessageContents if madeByMessage else None)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

    guildSettings[str(interaction.guild_id)] = {
        "hostPanel": host_panel.id,
        "hostRole": host_role.id,
        "matchesCatagory": matches_catagory.id,
        "queueVoiceChannel": queue.id,
        "blacklistRole": blacklist_role.id,
        "hostShout": host_shout.id
    }


    out, explain = await canBotOperate(guild=interaction.guild, checkSetup=True, explain=True)
    if not out:
        explanationText = "\n".join(f"- {e}" for e in explain)
        embed = discord.Embed(
            title="Server Setup Required",
            description=f"I can't operate properly in this server yet:\n\n{explanationText}",
            color=0xF1C40F  # Yellow/orange for warning
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        guildSettings[str(interaction.guild_id)] = {}
        await writeSettings(guild=interaction.guild)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await writeSettings(guild=interaction.guild)
    
    try:
        await interaction.response.defer(thinking=True, ephemeral=True)
        await setupHostPanel(channel=host_panel)

    except discord.Forbidden:
        embed = discord.Embed(
            title="Channel Access Issue",
            description=(
                f"The bot couldn't manage/send/view messages in {host_panel.mention}.\n\n"
                f"Please check the bot documentation for guidance [here]({botDocs}). "
                "Scroll down to the **Debug** section for troubleshooting steps."
        ),
        color=0xE67E22  # Orange for warnings
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.followup.send(embed=embed)
        return

    except Exception as e:
        embed = discord.Embed(
            title="Host Panel Error",
            description=(
                f"There was an error processing the host panel in {host_panel.mention}.\n\n"
                f"Please check the bot documentation for guidance [here]({botDocs}). "
                "Scroll down to the **Debug** section for troubleshooting steps."
            ),
            color=0xE74C3C  # Red for errors
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.followup.send(embed=embed, ephemeral=True)
        log("EXCEPTION", text=f"There was an error trying to proccess the host_panel in the server {interaction.guild.name}({interaction.guild_id}):\n{e}")
        return

    embed = discord.Embed(
    title="Operation Successful",
    description="The operation completed successfully!",
    color=0x2ECC71  # Green for success
    )
    embed.set_footer(text=madeByMessageContents if madeByMessage else None)
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="elo", description="check elo hee hee haha")
async def elo(interaction: discord.Interaction, person: discord.Member | None = None):

    out, explain = await canBotOperate(guild=interaction.guild, checkSetup=True, explain=True)
    if not out:
        explanationText = "\n".join(f"- {e}" for e in explain)
        embed = discord.Embed(
            title="Server Setup Required",
            description=f"I can't operate properly in this server yet:\n\n{explanationText}",
            color=0xF1C40F  # Yellow/orange for warning
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if not person:
        person = interaction.user


    await updateElo(person, person.guild, {}, True, True)

    personData = guildStats.get(str(person.guild.id), {}).get(str(person.id), {})
    won = personData.get("won", 0)
    played = personData.get("played", 0)
    lost = played - won
    elo = personData.get("elo", 100)
    hosted = personData.get("hosted", 0)
    embed = discord.Embed(title="Elo Profile",
                      colour=0xffffff)

    embed.set_author(name=stripTag(person.display_name),
                    icon_url=person.display_avatar.url)

    embed.add_field(name="🎮 matches",
                    value=f"played: {played}\nwon: {won}\nlost: {lost}\nhosted: {hosted}",
                    inline=False)
    embed.add_field(name="🏆Elo",
                    value=f"elo: {elo}",
                    inline=False)

    embed.set_footer(text=madeByMessageContents if madeByMessage else None)
    await interaction.response.send_message(embed=embed)
    await writeStats(guild=interaction.guild)

@bot.tree.command(name="penalty")
async def penalty(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str):
    perms = interaction.user.guild_permissions
    if not (perms.administrator or perms.manage_guild or perms.moderate_members):
        embed = discord.Embed(
            title="Invalid permissions",
            description="You need `administrator`, `manage_guild` or `moderate_members` permissions.",
            color=discord.Color.orange()
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    guild = interaction.guild
    guildId = str(guild.id)
    roleId = guildSettings.get(guildId, {}).get("blacklistRole")
    if not roleId:
        embed = discord.Embed(title="Error", description="Blacklist role not set.", color=discord.Color.red())
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    role = guild.get_role(roleId)
    if not role:
        embed = discord.Embed(title="Error", description="Blacklist role not found.", color=discord.Color.red())
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if role in member.roles:
        embed = discord.Embed(
            title="Already Penalized",
            description=f"{member.mention} is already under a penalty.",
            color=discord.Color.yellow()
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    total_seconds = parse_duration(duration)
    if total_seconds <= 0:
        embed = discord.Embed(title="Invalid duration", description="Provide a duration like `1d 2h 30m`.", color=discord.Color.red())
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await member.add_roles(role, reason=f"Penalty by {interaction.user} for {duration}")

    # Track penalty
    penaltyData.setdefault(guildId, {}).setdefault(member.id, []).append((datetime.utcnow(), total_seconds))

    embed = discord.Embed(title="Penalty Applied", description=f"{member.mention} penalized for {duration}.\nReason: {reason}", color=discord.Color.orange())
    embed.set_footer(text=madeByMessageContents if madeByMessage else None)
    await interaction.response.send_message(embed=embed)

    # Schedule removal
    async def remove_role_later():
        await asyncio.sleep(total_seconds)
        member_fresh = guild.get_member(member.id)
        role_fresh = guild.get_role(roleId)
        if member_fresh and role_fresh in member_fresh.roles:
            try:
                await member_fresh.remove_roles(role_fresh, reason="Penalty expired")
            except discord.Forbidden:
                pass

    bot.loop.create_task(remove_role_later())

# --------------------------
# Remove penalty manually
# --------------------------
@bot.tree.command(name="remove_penalty")
async def remove_penalty(interaction: discord.Interaction, member: discord.Member, reason: str):
    perms = interaction.user.guild_permissions
    if not (perms.administrator or perms.manage_guild or perms.moderate_members):
        embed = discord.Embed(
            title="Invalid permissions",
            description="You need `administrator`, `manage_guild` or `moderate_members` permissions.",
            color=discord.Color.orange()
        )
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    guild = interaction.guild
    guildId = str(guild.id)
    roleId = guildSettings.get(guildId, {}).get("blacklistRole")
    if not roleId:
        embed = discord.Embed(title="Error", description="Blacklist role not set.", color=discord.Color.red())
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    role = guild.get_role(roleId)
    if not role or role not in member.roles:
        embed = discord.Embed(title="No Active Penalty", description=f"{member.mention} is not currently penalized.", color=discord.Color.yellow())
        embed.set_footer(text=madeByMessageContents if madeByMessage else None)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await member.remove_roles(role, reason=f"Penalty manually removed by {interaction.user}")
    embed = discord.Embed(title="Penalty Removed", description=f"{member.mention}'s penalty has been removed.\nReason: {reason}", color=discord.Color.green())
    embed.set_footer(text=madeByMessageContents if madeByMessage else None)
    await interaction.response.send_message(embed=embed)

# run bot
with open("global_settings.json", "r") as f:
    token = json.load(f)["token"]

bot.run(token)