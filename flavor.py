from datetime import date, datetime
from zoneinfo import ZoneInfo

# Same home timezone the bot's reminders and nightly wrap-up already run in.
APP_TZ = ZoneInfo("America/Toronto")

CLOCK_FACES = ["🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚"]

TIME_OF_DAY = [
    (5, 11, "Early Bird Derivative"),
    (11, 17, "Midday Grind"),
    (17, 22, "Evening Integral"),
    (22, 24, "Certified Insomniac"),
    (0, 5, "Certified Insomniac"),
]

MATH_CAT_QUIPS = [
    "A cat's favorite branch of math is meow-trices.",
    "Schrödinger's study session: did you study, or didn't you? Log one to collapse the wavefunction.",
    "Cats have nine lives. You get one shot at today's goal — make it count.",
    "∫(cat naps) d(hours) diverges. Your study time doesn't have to.",
    "A cat always lands on its feet. Your proof should always land on QED.",
    "Why did the cat sit on the problem set? To keep your variables constant.",
    "The set of things a cat cares about does not include your deadline. It's an empty set, ∅.",
    "Cats are obligate carnivores. You're an obligate studier. Both non-negotiable.",
    "A cat ignoring you and a diverging series have the same energy.",
    "Every great proof starts the way a cat starts a nap: total commitment.",
    "It's always math o'clock somewhere.",
    "A clock has 12 faces and a cat has 9 lives — neither excuse is why you haven't logged today.",
]


def daily_quip() -> str:
    """Same quip all day, rotates day to day — deterministic so it doesn't flicker on refresh."""
    return MATH_CAT_QUIPS[date.today().toordinal() % len(MATH_CAT_QUIPS)]


def progress_flavor(done: int, goal: int) -> dict:
    """Emoji + headline + streak-trait + illustrated sprite for the current goal/done state."""
    ratio = (done / goal) if goal > 0 else (1.0 if done > 0 else 0.0)
    hour = datetime.now(APP_TZ).hour

    if done == 0:
        if hour >= 17:
            return {"emoji": "😿", "headline": "Time is ticking", "trait": "Anxious Stray", "sprite": "nudge"}
        return {"emoji": "😴", "headline": "Not a single problem logged yet", "trait": "Schrödinger's Studier", "sprite": "sleepy"}
    if ratio < 1.0:
        return {"emoji": "🐈", "headline": "Chipping away", "trait": "Determined Stray", "sprite": "pacing"}
    if ratio == 1.0:
        return {"emoji": "😻", "headline": "Goal crushed", "trait": "Precise Kitten", "sprite": "goal"}
    return {"emoji": "🙀", "headline": "Overachiever detected", "trait": "Menace to Mediocrity", "sprite": "berserk"}


def heatmap_level(done: int, goal: int) -> int:
    """0 (nothing) .. 4 (overachiever) — drives the contribution-heatmap color."""
    if done == 0:
        return 0
    if goal <= 0:
        return 2
    ratio = done / goal
    if ratio < 0.5:
        return 1
    if ratio < 1.0:
        return 2
    if ratio == 1.0:
        return 3
    return 4


def compute_badges(history: list[dict]) -> list[dict]:
    """history: [{date, goal, done}, ...] oldest first. Derived at read time — no stored state."""
    overachiever_days = sum(1 for h in history if h["goal"] > 0 and h["done"] > h["goal"])
    precise_days = sum(1 for h in history if h["goal"] > 0 and h["done"] == h["goal"])
    logged_days = sum(1 for h in history if h["done"] > 0)

    badges = []
    if overachiever_days >= 3:
        badges.append({"emoji": "🏅", "name": "Overachiever", "detail": "Completer for high-completion days"})
    if precise_days >= 5:
        badges.append({"emoji": "🎯", "name": "Precise Kitten", "detail": f"{precise_days} days hitting goal exactly"})
    if logged_days >= 30:
        badges.append({"emoji": "📚", "name": "Creature of Habit", "detail": f"{logged_days} days logged"})
    if not badges:
        badges.append({"emoji": "🐣", "name": "Just Getting Started", "detail": "Log a few more days to earn a badge"})
    return badges


def clock_snapshot() -> dict:
    """Current time in the app's home timezone, for the Mathoclock badge."""
    now = datetime.now(APP_TZ)
    label = next(l for start, end, l in TIME_OF_DAY if start <= now.hour < end)
    return {
        "face": CLOCK_FACES[now.hour % 12],
        "label": label,
        "time_str": now.strftime("%-I:%M %p"),
    }
