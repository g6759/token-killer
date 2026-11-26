"""Case identifiers shared by the game, batch runner and reference loader."""

CASE_COUNTS = (18, 19, 20, 26, 27, 37, 41, 50)
PACK_COUNTS = {f"square-{count}": count for count in CASE_COUNTS}
PACKS = tuple(PACK_COUNTS)
DEFAULT_BATCH_PACKS = ("square-26", "square-37", "square-50")
