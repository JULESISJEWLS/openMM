import itertools
import json
import secrets
import string
import discord
from colorama import Fore, Style
from datetime import datetime
import inspect

colorMap = {
        "LOG": Fore.BLUE,
        "DEBUG": Fore.CYAN,
        "INFO": Fore.GREEN,
        "SUCCESS": Fore.LIGHTGREEN_EX,
        "WARNING": Fore.YELLOW,
        "ERROR": Fore.RED,
        "CRITICAL": Fore.MAGENTA,
        "EXCEPTION": Fore.LIGHTRED_EX,
        "EVENT": Fore.LIGHTBLUE_EX,
        "COMMAND": Fore.LIGHTCYAN_EX,
        "SETUP": Fore.LIGHTYELLOW_EX,
        "SYSTEM": Fore.WHITE
    }

def generateShortId(currentUuids, length=6):
    """
    made with my best friend chat gpt.
    """
    alphabet = string.ascii_letters + string.digits  # a-zA-Z0-9
    while True:
        newId = ''.join(secrets.choice(alphabet) for _ in range(length))
        if newId not in currentUuids:
            return newId


def getTeams(eloData):
    """
    Splits players into two balanced teams based on per-player ELO.
    Returns per-player ELOs in the matchup so calculatePreGameEloPerPlayer works.
    """
    players = list(eloData.keys())
    n = len(players)
    teamSize = n // 2

    allTeamCombos = list(itertools.combinations(players, teamSize))
    seen = set()

    bestMatchup = None
    smallestDiff = float('inf')

    for team1 in allTeamCombos:
        team2 = tuple(sorted(set(players) - set(team1)))
        matchupKey = tuple(sorted([team1, team2]))
        if matchupKey in seen:
            continue
        seen.add(matchupKey)

        # Use per-player ELOs instead of sums
        eloRed = {p: eloData[p] for p in team1}
        eloBlue = {p: eloData[p] for p in team2}
        diff = abs(sum(eloRed.values()) - sum(eloBlue.values()))

        if diff < smallestDiff:
            smallestDiff = diff
            bestMatchup = {
                "redTeam": team1,
                "blueTeam": team2,
                "eloRedTeam": eloRed,
                "eloBlueTeam": eloBlue,
                "eloDifference": diff
            }

    return bestMatchup

def calculatePreGameElo(matchup, perPlayerK=30):
    """
    Returns predicted ELO changes per player before a match.
    perPlayerK: desired max ELO change per player.
    """
    redTeam = matchup["redTeam"]
    blueTeam = matchup["blueTeam"]
    eloRedTeam = matchup["eloRedTeam"]  # dict: {player: elo}
    eloBlueTeam = matchup["eloBlueTeam"]

    # Calculate average opposing team ELO for each player
    avgBlueElo = sum(eloBlueTeam.values()) / len(blueTeam)
    avgRedElo = sum(eloRedTeam.values()) / len(redTeam)

    redAdjustments = {}
    for player in redTeam:
        expectedScore = 1 / (1 + 10 ** ((avgBlueElo - eloRedTeam[player]) / 400))
        redAdjustments[player] = {
            "won": round(perPlayerK * (1 - expectedScore)),
            "loss": round(perPlayerK * (0 - expectedScore))
        }

    blueAdjustments = {}
    for player in blueTeam:
        expectedScore = 1 / (1 + 10 ** ((avgRedElo - eloBlueTeam[player]) / 400))
        blueAdjustments[player] = {
            "won": round(perPlayerK * (1 - expectedScore)),
            "loss": round(perPlayerK * (0 - expectedScore))
        }

    return {
        "redTeam": redAdjustments,
        "blueTeam": blueAdjustments
    }



def log(typ: str="LOG", text: str="", color: str | None = None) -> str:
    global colorMap
    """
    Logs a message to the console with timestamp, type, caller info, and optional coloring.

    Parameters:
        typ (str): The type/category of the log message (e.g., "INFO", "ERROR", "DEBUG").
                   Defaults to "LOG".
        text (str): The message content to log.
        color (str | None): Optional color override. If not provided, a color is automatically
                            selected based on the log type.

    Returns:
        str: The formatted log string.

    Features:
        - Displays timestamp in 'YYYY-MM-DD HH:MM:SS' format.
        - Shows the log type in a color corresponding to the type.
        - Prints the line number and function name where the log was called.
        - Uses colorama for terminal colors with auto-reset.
        - Returns the formatted log string in addition to printing it.

    Example:
        log("INFO", "Bot started successfully.")
        log("ERROR", "Failed to connect to database.", color=Fore.LIGHTRED_EX)

    made with my best friend chat gpt.
    """
    now = datetime.now()

    if not color:
        color = colorMap.get(typ.upper(), Fore.WHITE)

    formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")
    
    # Get caller info
    caller_frame = inspect.stack()[1]
    caller_name = caller_frame.function
    caller_file = caller_frame.filename.split("/")[-1]  # just filename
    caller_line = caller_frame.lineno

    message = (
        f"{Fore.BLACK}{formatted_time}{Style.RESET_ALL} "
        f"{color}{typ.upper()}{Style.RESET_ALL} "
        f"{Fore.MAGENTA}({caller_line} in {caller_name}){Style.RESET_ALL} "
        f"{text}"
    )

    print(message)
    return message


