"""Research pack registry shared by the lab, benchmark and local proof verifier."""

from pathlib import Path


PACKS = (
    "cap-set", "cap-set-5d", "cap-set-6d", "cap-set-7d", "cap-set-8d",
    "golomb-ruler", "golomb-ruler-12", "golomb-ruler-16", "lorenz-divergence",
)
DATABASE = Path("研究题") / "records.sqlite3"


def get_pack(pack_id):
    if pack_id not in PACKS:
        raise ValueError("Unknown research game / 未知研究题包")
    if pack_id == "lorenz-divergence":
        from .lorenz import get_pack as lorenz_pack
        return lorenz_pack()
    from .discrete import get_pack as discrete_pack
    return discrete_pack(pack_id)


def build_store(db_path):
    from relay_store import Store
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return Store(path, resolve_pack=get_pack)
