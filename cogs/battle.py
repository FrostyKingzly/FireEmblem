import asyncio
import functools
import logging
import os
import random
from collections import deque
from dataclasses import dataclass, field
from io import BytesIO
from typing import Dict, List, Literal, Optional, Set, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# 12x12 map with A-L columns and 1-12 rows.
GRID_COLUMNS = [chr(ord("A") + i) for i in range(12)]
GRID_SIZE = 12
CELL_SIZE = 96
GRID_LINE_WIDTH = 4
BOARD_PADDING = 4
BOARD_SPRITE_X_OFFSET = -2
BOARD_SPRITE_Y_OFFSET = -8
BOARD_BG = (216, 216, 216, 255)
GRID_COLOR = (0, 0, 0, 255)
HP_BAR_HEIGHT = 10
HP_BAR_SIDE_MARGIN = 6
HP_BAR_BOTTOM_MARGIN = 3
HP_BAR_BACKGROUND = (20, 20, 20, 230)
BACKGROUND_KEY_DISTANCE = 45
ENEMY_SPRITE_SIZE = (81, 103)

ASSET_DIR = "assets"
BACKGROUND_ASSET_PATH = os.path.join(ASSET_DIR, "backgrounds", "background.png")
CHARACTER_ASSET_DIR = os.path.join(ASSET_DIR, "characters")
ENEMY_ASSET_DIR = os.path.join(ASSET_DIR, "enemies")
BATTLE_SCENE_SIZE = (1280, 720)
BATTLE_SCENE_GRID_COLUMNS = 14
BATTLE_SCENE_GRID_ROWS = 6
BATTLE_SCENE_MELEE_PLAYER_RIGHT_PLANE = "H"
BATTLE_SCENE_MELEE_ENEMY_LEFT_PLANE = "J"
BATTLE_SCENE_RANGED_PLAYER_RIGHT_PLANE = "F"
BATTLE_SCENE_RANGED_ENEMY_LEFT_PLANE = "L"
BATTLE_SCENE_FOOTING_ROW = 5.5
BATTLE_SCENE_ENEMY_FORWARD_OFFSET_STEPS = 1.0
BATTLE_SCENE_ENEMY_UPWARD_OFFSET_STEPS = 0.0
TEST_MAP_IMAGE_PATH = os.path.join(ASSET_DIR, "backgrounds", "battle2_map.png")
STARTING_POSITIONS = ["1A", "1B", "2A", "2B"]
BATTLE2_STARTING_POSITIONS = ["11G", "11H", "12G", "12H"]

TERRAIN_PLAINS = "plains"
TERRAIN_WOODS = "woods"
TERRAIN_WATER = "water"
TERRAIN_CLIFFS = "cliffs"

TERRAIN_INFO = {
    TERRAIN_PLAINS: {"name": "Plains", "mov_cost": 1, "avoid": 0, "infantry_passable": True},
    TERRAIN_WOODS: {"name": "Woods", "mov_cost": 2, "avoid": 30, "infantry_passable": True},
    TERRAIN_WATER: {"name": "Water", "mov_cost": 2, "avoid": 0, "infantry_passable": False},
    TERRAIN_CLIFFS: {"name": "Cliffs", "mov_cost": 1, "avoid": 0, "infantry_passable": False},
}

BATTLE2_TERRAIN: Dict[str, str] = {
    "1C": TERRAIN_CLIFFS, "1D": TERRAIN_CLIFFS, "1K": TERRAIN_WOODS, "1L": TERRAIN_WOODS,
    "2C": TERRAIN_CLIFFS, "2D": TERRAIN_CLIFFS, "2K": TERRAIN_WOODS, "2L": TERRAIN_WOODS,
    "3K": TERRAIN_CLIFFS, "3L": TERRAIN_CLIFFS,
    "4K": TERRAIN_CLIFFS, "4L": TERRAIN_CLIFFS,
    "5A": TERRAIN_CLIFFS, "5B": TERRAIN_CLIFFS, "5C": TERRAIN_CLIFFS, "5D": TERRAIN_CLIFFS,
    "6A": TERRAIN_WATER, "6B": TERRAIN_WATER, "6C": TERRAIN_WATER, "6D": TERRAIN_WATER,
    "7C": TERRAIN_WATER, "7D": TERRAIN_WATER, "7E": TERRAIN_CLIFFS, "7F": TERRAIN_CLIFFS,
    "7I": TERRAIN_CLIFFS, "7J": TERRAIN_CLIFFS, "7K": TERRAIN_CLIFFS, "7L": TERRAIN_CLIFFS,
    "8C": TERRAIN_WATER, "8D": TERRAIN_WATER, "8E": TERRAIN_WATER, "8F": TERRAIN_WATER,
    "8I": TERRAIN_WATER, "8J": TERRAIN_WATER, "8K": TERRAIN_WATER, "8L": TERRAIN_WATER,
    "9I": TERRAIN_WOODS, "9J": TERRAIN_WOODS,
    "10J": TERRAIN_WOODS,
    "11C": TERRAIN_WOODS, "11D": TERRAIN_WOODS,
    "12C": TERRAIN_WOODS, "12D": TERRAIN_WOODS,
}


@dataclass
class UnitStats:
    hp: int
    strength: int
    magic: int
    dex: int
    spd: int
    defense: int
    res: int
    luck: int
    bld: int
    mov: int


WeaponKind = Literal["physical", "tome", "staff"]
EnemyBehavior = Literal["aggressive", "stationary", "provoke"]


@dataclass(frozen=True)
class Weapon:
    name: str
    might: int
    hit: int
    crit: int
    weight: int
    rng_min: int
    rng_max: int
    kind: WeaponKind = "physical"
    heal_power: int = 0
    inflicts_poison: bool = False
    crit_multiplier: float = 1.0
    targets_allies: bool = False


@dataclass
class Unit:
    name: str
    level: int
    klass: str
    stats: UnitStats
    coord: str
    image_name: Optional[str] = None
    current_hp: Optional[int] = None
    inventory: List[Weapon] = field(default_factory=list)
    equipped_index: int = 0
    behavior: EnemyBehavior = "aggressive"
    personal_skill: Optional[str] = None
    poison_stacks: int = 0

    def __post_init__(self) -> None:
        if self.current_hp is None:
            self.current_hp = self.stats.hp

    @property
    def equipped_weapon(self) -> Weapon:
        return self.inventory[self.equipped_index]


@dataclass(frozen=True)
class CharacterProfile:
    portrait_image_name: Optional[str] = None
    critical_quotes: Tuple[str, ...] = ()
    critical_image_name: Optional[str] = None
    critical_image_url: Optional[str] = None


@dataclass
class BattleState:
    players: Dict[str, Unit]
    enemies: Dict[str, Unit]
    phase: Literal["player", "enemy"] = "player"
    moved_this_turn: Set[str] = field(default_factory=set)
    active_battle_message_id: Optional[int] = None
    provoked_enemies: Set[str] = field(default_factory=set)
    battle_over: bool = False
    terrain: Dict[str, str] = field(default_factory=dict)
    starting_positions: Tuple[str, ...] = tuple(STARTING_POSITIONS)


# FE Engage-inspired class base stats storage. (Some classes intentionally not yet used by units.)
CLASS_BASE_STATS: Dict[str, UnitStats] = {
    "Sword Fighter": UnitStats(20, 5, 0, 7, 8, 3, 2, 2, 5, 4),
    "Swordmaster": UnitStats(21, 6, 1, 9, 11, 4, 3, 4, 6, 5),
    "Hero": UnitStats(23, 8, 0, 8, 9, 5, 2, 3, 7, 5),
    "Lance Fighter": UnitStats(23, 7, 2, 8, 6, 4, 2, 2, 5, 4),
    "Halberdier": UnitStats(24, 9, 1, 9, 7, 6, 2, 3, 6, 5),
    "Royal Knight": UnitStats(23, 7, 5, 9, 8, 5, 4, 5, 6, 6),
    "Axe Fighter": UnitStats(26, 9, 0, 5, 5, 3, 1, 1, 7, 4),
    "Berserker": UnitStats(29, 13, 0, 6, 6, 3, 2, 2, 9, 5),
    "Warrior": UnitStats(27, 12, 1, 7, 7, 4, 3, 2, 8, 5),
    "Archer": UnitStats(19, 6, 0, 9, 5, 2, 1, 2, 4, 4),
    "Sniper": UnitStats(22, 8, 1, 11, 6, 3, 1, 3, 5, 5),
    "Bow Knight": UnitStats(22, 7, 2, 10, 8, 3, 3, 3, 5, 6),
    "Sword Armor": UnitStats(25, 8, 0, 6, 1, 12, 0, 2, 7, 4),
    "Lance Armor": UnitStats(25, 8, 0, 6, 1, 12, 0, 2, 7, 4),
    "Axe Armor": UnitStats(25, 8, 0, 6, 1, 12, 0, 2, 7, 4),
    "General": UnitStats(28, 11, 1, 7, 2, 14, 1, 3, 10, 4),
    "Great Knight": UnitStats(26, 9, 2, 8, 5, 13, 2, 3, 8, 6),
    "Sword Cavalier": UnitStats(23, 6, 1, 8, 7, 4, 2, 2, 6, 5),
    "Lance Cavalier": UnitStats(23, 6, 1, 8, 7, 4, 2, 2, 6, 5),
    "Axe Cavalier": UnitStats(23, 6, 1, 8, 7, 4, 2, 2, 6, 5),
    "Paladin": UnitStats(25, 8, 2, 10, 8, 6, 3, 3, 7, 6),
    "Wolf Knight": UnitStats(23, 6, 3, 9, 10, 4, 4, 4, 6, 6),
    "Mage": UnitStats(18, 1, 7, 6, 6, 1, 7, 2, 4, 4),
    "Sage": UnitStats(20, 1, 9, 8, 7, 3, 9, 3, 5, 5),
    "Mage Knight": UnitStats(21, 5, 7, 8, 9, 3, 8, 2, 6, 6),
    "Martial Monk": UnitStats(18, 3, 5, 6, 5, 3, 8, 3, 3, 4),
    "Martial Master": UnitStats(22, 6, 5, 6, 5, 4, 7, 4, 6, 5),
    "High Priest": UnitStats(20, 3, 8, 8, 6, 3, 10, 5, 4, 5),
    "Sword Flier": UnitStats(21, 5, 2, 7, 9, 3, 7, 3, 4, 5),
    "Lance Flier": UnitStats(21, 5, 2, 7, 9, 3, 7, 3, 4, 5),
    "Axe Flier": UnitStats(21, 5, 2, 7, 9, 3, 7, 3, 4, 5),
    "Griffin Knight": UnitStats(22, 7, 3, 10, 11, 4, 9, 5, 5, 6),
    "Wyvern Knight": UnitStats(25, 9, 1, 8, 9, 6, 5, 3, 6, 6),
    "Thief": UnitStats(22, 5, 0, 10, 10, 6, 2, 2, 4, 5),
    "Dancer": UnitStats(21, 5, 1, 8, 8, 2, 5, 5, 5, 5),
    "Fell Child (DLC)": UnitStats(20, 5, 5, 5, 5, 5, 5, 5, 5, 5),
    "Melusine": UnitStats(22, 7, 8, 6, 8, 6, 9, 2, 6, 6),
    "Enchanter": UnitStats(20, 5, 5, 5, 5, 5, 5, 5, 5, 5),
    "Mage Cannoneer": UnitStats(20, 5, 5, 5, 5, 5, 5, 5, 5, 5),
}


WEAPONS: Dict[str, Weapon] = {
    "Iron Sword": Weapon(name="Iron Sword", might=5, hit=90, crit=0, weight=5, rng_min=1, rng_max=1),
    "Killing Edge": Weapon(name="Killing Edge", might=9, hit=75, crit=30, weight=10, rng_min=1, rng_max=1, crit_multiplier=1.5),
    "Iron Dagger": Weapon(name="Iron Dagger", might=5, hit=100, crit=0, weight=3, rng_min=1, rng_max=2, inflicts_poison=True),
    "Fire": Weapon(name="Fire", might=5, hit=95, crit=0, weight=4, rng_min=1, rng_max=2, kind="tome"),
    "Heal": Weapon(name="Heal", might=0, hit=100, crit=0, weight=0, rng_min=1, rng_max=1, kind="staff", heal_power=10, targets_allies=True),
    # Stored tome examples so future users inherit the correct range data model.
    "Thunder": Weapon(name="Thunder", might=5, hit=80, crit=0, weight=10, rng_min=1, rng_max=3, kind="tome"),
    "Wind": Weapon(name="Wind", might=4, hit=90, crit=0, weight=4, rng_min=1, rng_max=2, kind="tome"),
    "Elfire": Weapon(name="Elfire", might=11, hit=90, crit=0, weight=7, rng_min=1, rng_max=2, kind="tome"),
    "Thoron": Weapon(name="Thoron", might=18, hit=70, crit=0, weight=16, rng_min=1, rng_max=3, kind="tome"),
    "Meteor": Weapon(name="Meteor", might=13, hit=80, crit=0, weight=20, rng_min=3, rng_max=7, kind="tome"),
}


PLAYER_UNITS: List[Unit] = [
    Unit(
        name="Acheron",
        level=1,
        klass="Sword Fighter",
        stats=CLASS_BASE_STATS["Sword Fighter"],
        coord="1A",
        image_name="acheron.png",
        inventory=[WEAPONS["Killing Edge"]],
        personal_skill="Poison Hunter",
    ),
    Unit(
        name="Vander",
        level=1,
        klass="Martial Monk",
        stats=CLASS_BASE_STATS["Martial Monk"],
        coord="1B",
        image_name="vander.png",
        inventory=[WEAPONS["Heal"]],
    ),
    Unit(
        name="Clanne",
        level=1,
        klass="Thief",
        stats=CLASS_BASE_STATS["Thief"],
        coord="2A",
        image_name="clanne.png",
        inventory=[WEAPONS["Iron Dagger"]],
    ),
    Unit(
        name="Framme",
        level=1,
        klass="Mage",
        stats=CLASS_BASE_STATS["Mage"],
        coord="2B",
        image_name="framme.png",
        inventory=[WEAPONS["Fire"]],
        personal_skill="Toxic Tome",
    ),
]

CHARACTER_PROFILES: Dict[str, CharacterProfile] = {
    "Acheron": CharacterProfile(
        portrait_image_name="acheron.png",
        critical_quotes=("I weep for the departed.",),
        critical_image_name="acheron_critical.png",
        critical_image_url="https://cdn.discordapp.com/attachments/1478853056962625668/1487620310424486030/08340B99-085E-4DCC-AB8C-508D689FBEF1.jpg?ex=69c9cde0&is=69c87c60&hm=cf7bda108c7ec12888af7998a3eb2cdc76b7f6e3baaac14b521d97d374e3dd1a&",
    ),
    "Vander": CharacterProfile(portrait_image_name="vander.png"),
    "Clanne": CharacterProfile(portrait_image_name="clanne.png"),
    "Framme": CharacterProfile(portrait_image_name="framme.png"),
}

ENEMY_UNITS: List[Unit] = [
    Unit(
        "Sword Fighter 1",
        1,
        "Sword Fighter",
        CLASS_BASE_STATS["Sword Fighter"],
        "12L",
        image_name="sword_fighter.png",
        behavior="aggressive",
        inventory=[WEAPONS["Iron Sword"]],
    ),
    Unit(
        "Sword Fighter 2",
        1,
        "Sword Fighter",
        CLASS_BASE_STATS["Sword Fighter"],
        "12K",
        image_name="sword_fighter.png",
        behavior="aggressive",
        inventory=[WEAPONS["Iron Sword"]],
    ),
    Unit(
        "Sword Fighter 3",
        1,
        "Sword Fighter",
        CLASS_BASE_STATS["Sword Fighter"],
        "11L",
        image_name="sword_fighter.png",
        behavior="aggressive",
        inventory=[WEAPONS["Iron Sword"]],
    ),
    Unit(
        "Sword Fighter 4",
        1,
        "Sword Fighter",
        CLASS_BASE_STATS["Sword Fighter"],
        "11K",
        image_name="sword_fighter.png",
        behavior="aggressive",
        inventory=[WEAPONS["Iron Sword"]],
    ),
]

PLAYER_UNITS_BY_NAME: Set[str] = {unit.name for unit in PLAYER_UNITS}
ENEMY_UNITS_BY_NAME: Set[str] = {unit.name for unit in ENEMY_UNITS}

BATTLE2_ENEMY_UNITS: List[Unit] = [
    Unit("Sword Fighter 1", 1, "Sword Fighter", CLASS_BASE_STATS["Sword Fighter"], "1G", image_name="sword_fighter.png", behavior="aggressive", inventory=[WEAPONS["Iron Sword"]]),
    Unit("Sword Fighter 2", 1, "Sword Fighter", CLASS_BASE_STATS["Sword Fighter"], "3B", image_name="sword_fighter.png", behavior="aggressive", inventory=[WEAPONS["Iron Sword"]]),
    Unit("Sword Fighter 3", 1, "Sword Fighter", CLASS_BASE_STATS["Sword Fighter"], "3J", image_name="sword_fighter.png", behavior="aggressive", inventory=[WEAPONS["Iron Sword"]]),
    Unit("Sword Fighter 4", 1, "Sword Fighter", CLASS_BASE_STATS["Sword Fighter"], "6E", image_name="sword_fighter.png", behavior="aggressive", inventory=[WEAPONS["Iron Sword"]]),
    Unit("Sword Fighter 5", 1, "Sword Fighter", CLASS_BASE_STATS["Sword Fighter"], "6G", image_name="sword_fighter.png", behavior="aggressive", inventory=[WEAPONS["Iron Sword"]]),
    Unit("Sword Fighter 6", 1, "Sword Fighter", CLASS_BASE_STATS["Sword Fighter"], "9A", image_name="sword_fighter.png", behavior="aggressive", inventory=[WEAPONS["Iron Sword"]]),
]


def coord_to_xy(coord: str) -> Tuple[int, int]:
    row_str = coord[:-1]
    col_letter = coord[-1].upper()
    row = int(row_str)
    col = GRID_COLUMNS.index(col_letter) + 1
    return row, col


def xy_to_coord(row: int, col: int) -> str:
    return f"{row}{GRID_COLUMNS[col - 1]}"


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def move_coord(coord: str, direction: str) -> str:
    row, col = coord_to_xy(coord)
    if direction == "left":
        col -= 1
    elif direction == "right":
        col += 1
    elif direction == "up":
        row -= 1
    elif direction == "down":
        row += 1

    row = clamp(row, 1, GRID_SIZE)
    col = clamp(col, 1, GRID_SIZE)
    return xy_to_coord(row, col)


def manhattan_distance(a: str, b: str) -> int:
    ar, ac = coord_to_xy(a)
    br, bc = coord_to_xy(b)
    return abs(ar - br) + abs(ac - bc)


def in_weapon_range(attacker: Unit, defender: Unit) -> bool:
    distance = manhattan_distance(attacker.coord, defender.coord)
    weapon = attacker.equipped_weapon
    return weapon.rng_min <= distance <= weapon.rng_max


def attack_speed(unit: Unit) -> int:
    return unit.stats.spd - max(0, unit.equipped_weapon.weight - unit.stats.bld)


def calc_hit(attacker: Unit, defender: Unit) -> int:
    hit = attacker.equipped_weapon.hit + (attacker.stats.dex * 2) + (attacker.stats.luck // 2)
    avoid = (attack_speed(defender) * 2) + defender.stats.luck
    return clamp(hit - avoid, 0, 100)


def calc_crit(attacker: Unit, defender: Unit) -> int:
    crit = attacker.equipped_weapon.crit + (attacker.stats.dex // 2)
    crit = round(crit * attacker.equipped_weapon.crit_multiplier)
    # Personal skill: Acheron gets +50% crit when attacking poisoned targets.
    if attacker.name == "Acheron" and defender.poison_stacks > 0:
        crit = round(crit * 1.5)
    crit_avoid = defender.stats.luck
    return clamp(crit - crit_avoid, 0, 100)


def poison_bonus_damage(stacks: int) -> int:
    # FE Engage poison bonus: +1 / +3 / +5 damage taken.
    return {0: 0, 1: 1, 2: 3, 3: 5}.get(clamp(stacks, 0, 3), 0)


def calc_damage(attacker: Unit, defender: Unit) -> int:
    weapon = attacker.equipped_weapon
    offensive_stat = attacker.stats.magic if weapon.kind in {"tome", "staff"} else attacker.stats.strength
    target_defense = defender.stats.res if weapon.kind in {"tome", "staff"} else defender.stats.defense
    atk = offensive_stat + weapon.might
    return max(0, atk - target_defense + poison_bonus_damage(defender.poison_stacks))


def hp_bar(after_hp: int, max_hp: int, *, width: int = 10, fill_block: str = "🟦") -> str:
    fill = round((after_hp / max_hp) * width) if max_hp else 0
    fill = clamp(fill, 0, width)
    return fill_block * fill + "⬜" * (width - fill)


def clone_unit(base: Unit) -> Unit:
    return Unit(
        name=base.name,
        level=base.level,
        klass=base.klass,
        stats=base.stats,
        coord=base.coord,
        image_name=base.image_name,
        current_hp=base.stats.hp,
        inventory=list(base.inventory),
        equipped_index=0,
        behavior=base.behavior,
        personal_skill=base.personal_skill,
    )


def occupied_coords(state: BattleState, *, ignore_player: Optional[str] = None, ignore_enemy: Optional[str] = None) -> Set[str]:
    occupied: Set[str] = set()
    for player in state.players.values():
        if player.name != ignore_player:
            occupied.add(player.coord)
    for enemy in state.enemies.values():
        if enemy.name != ignore_enemy:
            occupied.add(enemy.coord)
    return occupied


def in_bounds(row: int, col: int) -> bool:
    return 1 <= row <= GRID_SIZE and 1 <= col <= GRID_SIZE


def weapon_targets_allies(unit: Unit) -> bool:
    weapon = unit.equipped_weapon
    return weapon.targets_allies or weapon.kind == "staff"


def terrain_at(state: BattleState, coord: str) -> str:
    return state.terrain.get(coord, TERRAIN_PLAINS)


def terrain_info(state: BattleState, coord: str) -> Dict[str, object]:
    return TERRAIN_INFO[terrain_at(state, coord)]


def is_infantry_passable(state: BattleState, coord: str) -> bool:
    return bool(terrain_info(state, coord)["infantry_passable"])


def terrain_movement_cost(state: BattleState, coord: str) -> int:
    return int(terrain_info(state, coord)["mov_cost"])


def movement_range(state: BattleState, unit: Unit) -> Set[str]:
    reachable: Set[str] = {unit.coord}
    blocked = occupied_coords(state, ignore_player=unit.name, ignore_enemy=unit.name)
    best_cost: Dict[str, int] = {unit.coord: 0}
    queue: deque[Tuple[str, int]] = deque([(unit.coord, 0)])

    while queue:
        current, spent = queue.popleft()
        row, col = coord_to_xy(current)
        for nr, nc in ((row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)):
            if not in_bounds(nr, nc):
                continue
            nxt = xy_to_coord(nr, nc)
            if nxt in blocked or not is_infantry_passable(state, nxt):
                continue
            new_cost = spent + terrain_movement_cost(state, nxt)
            if new_cost > unit.stats.mov:
                continue
            previous = best_cost.get(nxt)
            if previous is not None and previous <= new_cost:
                continue
            best_cost[nxt] = new_cost
            reachable.add(nxt)
            queue.append((nxt, new_cost))

    return reachable


def weapon_range_from_origins(origins: Set[str], weapon: Weapon) -> Set[str]:
    ranged_tiles: Set[str] = set()
    for origin in origins:
        origin_row, origin_col = coord_to_xy(origin)
        for row in range(1, GRID_SIZE + 1):
            for col in range(1, GRID_SIZE + 1):
                if not in_bounds(row, col):
                    continue
                distance = abs(origin_row - row) + abs(origin_col - col)
                if weapon.rng_min <= distance <= weapon.rng_max:
                    ranged_tiles.add(xy_to_coord(row, col))
    return ranged_tiles


def movement_and_action_ranges(state: BattleState, unit: Unit) -> Tuple[Set[str], Set[str], bool]:
    move_tiles = movement_range(state, unit)
    action_tiles = weapon_range_from_origins(move_tiles, unit.equipped_weapon)
    action_tiles -= move_tiles
    return move_tiles, action_tiles, weapon_targets_allies(unit)


def full_threat_range(state: BattleState, unit: Unit) -> Set[str]:
    move_tiles = movement_range(state, unit)
    return weapon_range_from_origins(move_tiles, unit.equipped_weapon)


def resolve_board_background_path(state: BattleState) -> Optional[str]:
    if not state.terrain:
        return None
    return resolve_asset_case_insensitive(os.path.dirname(TEST_MAP_IMAGE_PATH), os.path.basename(TEST_MAP_IMAGE_PATH))


def create_base_grid(*, background_path: Optional[str] = None) -> Image.Image:
    return _create_base_grid_template(background_path).copy()


@functools.lru_cache(maxsize=8)
def _create_base_grid_template(background_path: Optional[str] = None) -> Image.Image:
    side = GRID_SIZE * CELL_SIZE + GRID_LINE_WIDTH
    if background_path:
        map_image = Image.open(background_path).convert("RGBA")
        if map_image.size != (side, side):
            map_image = map_image.resize((side, side), Image.Resampling.LANCZOS)
        img = map_image
    else:
        img = Image.new("RGBA", (side, side), BOARD_BG)
    draw = ImageDraw.Draw(img)
    for i in range(GRID_SIZE + 1):
        p = i * CELL_SIZE
        draw.line([(p, 0), (p, side)], fill=GRID_COLOR, width=GRID_LINE_WIDTH)
        draw.line([(0, p), (side, p)], fill=GRID_COLOR, width=GRID_LINE_WIDTH)
    return img


def cell_origin(coord: str) -> Tuple[int, int]:
    row, col = coord_to_xy(coord)
    x = (col - 1) * CELL_SIZE + BOARD_PADDING
    y = (row - 1) * CELL_SIZE + BOARD_PADDING
    return x, y


def color_distance(a: Tuple[int, int, int], b: Tuple[int, int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def remove_solid_background(sprite: Image.Image) -> Image.Image:
    rgba = sprite.convert("RGBA")
    width, height = rgba.size
    if width == 0 or height == 0:
        return rgba

    pixels = rgba.load()
    corners = {
        pixels[0, 0][:3],
        pixels[width - 1, 0][:3],
        pixels[0, height - 1][:3],
        pixels[width - 1, height - 1][:3],
    }
    if not corners:
        return rgba

    visited = [[False for _ in range(width)] for _ in range(height)]
    queue: deque[Tuple[int, int]] = deque()
    for x in range(width):
        queue.append((x, 0))
        queue.append((x, height - 1))
    for y in range(height):
        queue.append((0, y))
        queue.append((width - 1, y))

    while queue:
        x, y = queue.popleft()
        if visited[y][x]:
            continue
        visited[y][x] = True
        r, g, b, a = pixels[x, y]
        if a == 0:
            continue
        rgb = (r, g, b)
        if min(color_distance(rgb, corner) for corner in corners) > BACKGROUND_KEY_DISTANCE:
            continue

        pixels[x, y] = (r, g, b, 0)
        if x > 0:
            queue.append((x - 1, y))
        if x + 1 < width:
            queue.append((x + 1, y))
        if y > 0:
            queue.append((x, y - 1))
        if y + 1 < height:
            queue.append((x, y + 1))

    return rgba


def load_sprite_from_assets(unit: Unit) -> Optional[Image.Image]:
    sprite_path = resolve_asset_for_unit(unit)
    if sprite_path is None:
        return None
    return load_cached_rgba_image(sprite_path).copy()


@functools.lru_cache(maxsize=64)
def load_cached_rgba_image(path: str) -> Image.Image:
    return Image.open(path).convert("RGBA")


def profile_for_unit(unit: Unit) -> CharacterProfile:
    return CHARACTER_PROFILES.get(unit.name, CharacterProfile(portrait_image_name=unit.image_name))


def resolve_asset_for_unit(unit: Unit, *, use_critical_image: bool = False) -> Optional[str]:
    profile = profile_for_unit(unit)
    candidate_names: List[str] = []
    if use_critical_image and profile.critical_image_name:
        candidate_names.append(profile.critical_image_name)
    if profile.portrait_image_name:
        candidate_names.append(profile.portrait_image_name)
    if unit.image_name:
        candidate_names.append(unit.image_name)
    candidate_names.append(f"{unit.name.lower().replace(' ', '_')}.png")
    candidate_names.append(f"{unit.name.lower()}.png")
    candidate_names.append(f"{unit.name}.png")

    seen: Set[str] = set()
    for filename in candidate_names:
        if filename in seen:
            continue
        seen.add(filename)
        path = os.path.join(ASSET_DIR, filename)
        if os.path.exists(path):
            return path
    return None


def random_critical_quote(unit: Unit) -> str:
    profile = profile_for_unit(unit)
    if not profile.critical_quotes:
        return f"{unit.name} strikes with lethal precision!"
    return random.choice(profile.critical_quotes)


def resolve_battle_scene_character_asset(unit: Unit) -> Optional[str]:
    candidate_names: List[str] = []
    if unit.name == "Acheron":
        candidate_names.append("acheron_battle.png")
    candidate_names.append(f"{unit.name.lower().replace(' ', '_')}_battle.png")
    candidate_names.append(f"{unit.name.lower()}_battle.png")

    for filename in candidate_names:
        path = resolve_asset_case_insensitive(CHARACTER_ASSET_DIR, filename)
        if path is not None:
            return path
    return None


def resolve_battle_scene_enemy_asset(unit: Unit) -> Optional[str]:
    explicit_enemy = resolve_asset_case_insensitive(ENEMY_ASSET_DIR, "enemy.png")
    if explicit_enemy is not None:
        return explicit_enemy

    candidate_names = [
        f"{unit.name.lower().replace(' ', '_')}.png",
        f"{unit.name.lower()}.png",
        "enemy.png",
    ]
    for filename in candidate_names:
        path = resolve_asset_case_insensitive(ENEMY_ASSET_DIR, filename)
        if path is not None:
            return path
    return None


def resolve_asset_case_insensitive(directory: str, filename: str) -> Optional[str]:
    exact_path = os.path.join(directory, filename)
    if os.path.exists(exact_path):
        return exact_path
    if not os.path.isdir(directory):
        return None
    target = filename.casefold()
    for entry in os.listdir(directory):
        if entry.casefold() == target:
            return os.path.join(directory, entry)
    return None


def render_battle_scene(attacker: Unit, defender: Unit) -> Optional[BytesIO]:
    background_path = resolve_asset_case_insensitive(os.path.dirname(BACKGROUND_ASSET_PATH), os.path.basename(BACKGROUND_ASSET_PATH))
    if background_path is None:
        return None

    background = Image.open(background_path).convert("RGBA")
    scene = background.resize(BATTLE_SCENE_SIZE, Image.Resampling.LANCZOS)

    player_unit = attacker if attacker.name in PLAYER_UNITS_BY_NAME else defender
    enemy_unit = defender if player_unit is attacker else attacker
    player_asset = resolve_battle_scene_character_asset(player_unit)
    enemy_asset = resolve_battle_scene_enemy_asset(enemy_unit)
    if player_asset is None or enemy_asset is None:
        return None

    player_sprite = Image.open(player_asset).convert("RGBA")
    enemy_sprite = Image.open(enemy_asset).convert("RGBA")

    combat_distance = manhattan_distance(attacker.coord, defender.coord)
    player_pos, enemy_pos = calculate_battle_scene_positions(
        scene_size=scene.size,
        player_sprite_size=player_sprite.size,
        enemy_sprite_size=enemy_sprite.size,
        ranged=combat_distance >= 2,
    )

    scene.alpha_composite(player_sprite, player_pos)
    scene.alpha_composite(enemy_sprite, enemy_pos)

    output = BytesIO()
    scene.save(output, format="PNG")
    output.seek(0)
    return output


def prepare_sprite_for_board(unit: Unit, sprite: Image.Image, *, enemy_sprite_size: Tuple[int, int] = ENEMY_SPRITE_SIZE) -> Image.Image:
    sprite_rgba = sprite.convert("RGBA")
    if unit.name in ENEMY_UNITS_BY_NAME and sprite_rgba.size != enemy_sprite_size:
        sprite_rgba = sprite_rgba.resize(enemy_sprite_size, Image.Resampling.LANCZOS)
    return sprite_rgba


def battle_scene_plane_x(scene_width: int, plane: str) -> int:
    plane_index = ord(plane.upper()) - ord("A")
    plane_index = max(0, min(BATTLE_SCENE_GRID_COLUMNS - 1, plane_index))
    cell_width = scene_width / BATTLE_SCENE_GRID_COLUMNS
    return round(plane_index * cell_width)


def battle_scene_footing_y(scene_height: int) -> int:
    cell_height = scene_height / BATTLE_SCENE_GRID_ROWS
    return round(BATTLE_SCENE_FOOTING_ROW * cell_height)


def calculate_battle_scene_positions(
    *,
    scene_size: Tuple[int, int],
    player_sprite_size: Tuple[int, int],
    enemy_sprite_size: Tuple[int, int],
    ranged: bool,
) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    scene_width, scene_height = scene_size
    player_width, player_height = player_sprite_size
    enemy_width, enemy_height = enemy_sprite_size
    cell_width = scene_width / BATTLE_SCENE_GRID_COLUMNS
    cell_height = scene_height / BATTLE_SCENE_GRID_ROWS

    if ranged:
        player_plane = BATTLE_SCENE_RANGED_PLAYER_RIGHT_PLANE
        enemy_plane = BATTLE_SCENE_RANGED_ENEMY_LEFT_PLANE
    else:
        player_plane = BATTLE_SCENE_MELEE_PLAYER_RIGHT_PLANE
        enemy_plane = BATTLE_SCENE_MELEE_ENEMY_LEFT_PLANE

    player_x = battle_scene_plane_x(scene_width, player_plane) - player_width
    enemy_x = battle_scene_plane_x(scene_width, enemy_plane) - round(
        BATTLE_SCENE_ENEMY_FORWARD_OFFSET_STEPS * cell_width
    )
    footing_y = battle_scene_footing_y(scene_height)
    player_y = footing_y - player_height
    enemy_y = footing_y - enemy_height - round(BATTLE_SCENE_ENEMY_UPWARD_OFFSET_STEPS * cell_height)
    return (player_x, player_y), (enemy_x, enemy_y)


def draw_fallback_unit(draw: ImageDraw.ImageDraw, coord: str, color: Tuple[int, int, int, int]) -> None:
    x, y = cell_origin(coord)
    cx = x + CELL_SIZE // 2
    cy = y + CELL_SIZE // 2
    radius = CELL_SIZE // 3
    draw.ellipse([(cx - radius, cy - radius), (cx + radius, cy + radius)], fill=color, outline=(0, 0, 0, 255), width=3)


def draw_unit_hp_bar(
    draw: ImageDraw.ImageDraw,
    unit: Unit,
    *,
    fill_color: Tuple[int, int, int, int],
) -> None:
    max_hp = max(1, unit.stats.hp)
    current_hp = max(0, min(unit.current_hp or 0, max_hp))
    hp_ratio = current_hp / max_hp

    x, y = cell_origin(unit.coord)
    left = x + HP_BAR_SIDE_MARGIN
    right = x + CELL_SIZE - BOARD_PADDING * 2 - HP_BAR_SIDE_MARGIN
    bottom = y + CELL_SIZE - BOARD_PADDING * 2 - HP_BAR_BOTTOM_MARGIN
    top = bottom - HP_BAR_HEIGHT

    draw.rectangle([(left, top), (right, bottom)], fill=HP_BAR_BACKGROUND)
    if hp_ratio <= 0:
        return

    inner_right = left + round((right - left) * hp_ratio)
    draw.rectangle([(left, top), (inner_right, bottom)], fill=fill_color)


def render_battle_map(
    state: BattleState,
    *,
    show_player_spaces: bool = False,
    visible_player_names: Optional[Set[str]] = None,
    highlight_move_coords: Optional[Set[str]] = None,
    highlight_action_coords: Optional[Set[str]] = None,
    highlight_ally_coords: Optional[Set[str]] = None,
    action_color: Tuple[int, int, int, int] = (220, 38, 38, 100),
    action_outline: Tuple[int, int, int, int] = (153, 27, 27, 255),
) -> BytesIO:
    board = create_base_grid(background_path=resolve_board_background_path(state))
    draw = ImageDraw.Draw(board)

    if show_player_spaces:
        for coord in state.starting_positions:
            x, y = cell_origin(coord)
            draw.rectangle(
                [(x, y), (x + CELL_SIZE - BOARD_PADDING * 2, y + CELL_SIZE - BOARD_PADDING * 2)],
                fill=(59, 130, 246, 90),
                outline=(30, 64, 175, 255),
                width=4,
            )

    if highlight_move_coords:
        for coord in sorted(highlight_move_coords):
            x, y = cell_origin(coord)
            draw.rectangle(
                [(x, y), (x + CELL_SIZE - BOARD_PADDING * 2, y + CELL_SIZE - BOARD_PADDING * 2)],
                fill=(56, 189, 248, 90),
                outline=(2, 132, 199, 255),
                width=4,
            )

    if highlight_action_coords:
        for coord in sorted(highlight_action_coords):
            x, y = cell_origin(coord)
            draw.rectangle(
                [(x, y), (x + CELL_SIZE - BOARD_PADDING * 2, y + CELL_SIZE - BOARD_PADDING * 2)],
                fill=action_color,
                outline=action_outline,
                width=4,
            )

    if highlight_ally_coords:
        for coord in sorted(highlight_ally_coords):
            x, y = cell_origin(coord)
            draw.rectangle(
                [(x, y), (x + CELL_SIZE - BOARD_PADDING * 2, y + CELL_SIZE - BOARD_PADDING * 2)],
                fill=(22, 163, 74, 115),
                outline=(21, 128, 61, 255),
                width=4,
            )

    for enemy in state.enemies.values():
        sprite = load_sprite_from_assets(enemy)
        if sprite is None:
            draw_fallback_unit(draw, enemy.coord, (220, 50, 50, 255))
        else:
            sprite = prepare_sprite_for_board(enemy, sprite)
            x, y = cell_origin(enemy.coord)
            px = x + (CELL_SIZE - sprite.width) // 2 + BOARD_SPRITE_X_OFFSET
            py = y + (CELL_SIZE - sprite.height) // 2 + BOARD_SPRITE_Y_OFFSET
            board.alpha_composite(sprite, (px, py))
        draw_unit_hp_bar(draw, enemy, fill_color=(220, 38, 38, 255))

    for player in state.players.values():
        if visible_player_names is not None and player.name not in visible_player_names:
            continue
        sprite = load_sprite_from_assets(player)
        if sprite is None:
            draw_fallback_unit(draw, player.coord, (50, 120, 220, 255))
        else:
            sprite = prepare_sprite_for_board(player, sprite)
            x, y = cell_origin(player.coord)
            px = x + (CELL_SIZE - sprite.width) // 2 + BOARD_SPRITE_X_OFFSET
            py = y + (CELL_SIZE - sprite.height) // 2 + BOARD_SPRITE_Y_OFFSET
            board.alpha_composite(sprite, (px, py))
        draw_unit_hp_bar(draw, player, fill_color=(37, 99, 235, 255))

    buf = BytesIO()
    board.save(buf, format="PNG")
    buf.seek(0)
    return buf


def render_test_map() -> BytesIO:
    background_path = resolve_asset_case_insensitive(os.path.dirname(TEST_MAP_IMAGE_PATH), os.path.basename(TEST_MAP_IMAGE_PATH))
    board = create_base_grid(background_path=background_path)

    buf = BytesIO()
    board.save(buf, format="PNG")
    buf.seek(0)
    return buf


def state_summary(state: BattleState) -> str:
    moved = ", ".join(sorted(state.moved_this_turn)) if state.moved_this_turn else "None"
    phase_label = "Player Phase" if state.phase == "player" else "Enemy Phase"
    return "\n".join([
        f"## {phase_label}",
        f"### Player Units: **{len(state.players)}**",
        f"### Enemy Units: **{len(state.enemies)}**",
        f"\nActed this phase: **{moved}**",
    ])


def unit_info_block(unit: Unit) -> str:
    stats = unit.stats
    inventory = ", ".join(w.name for w in unit.inventory) if unit.inventory else "None"
    return "\n".join(
        [
            f"Name: **{unit.name}**",
            f"Class: **{unit.klass}** Lv {unit.level}",
            f"HP: **{unit.current_hp}/{stats.hp}**",
            f"STR/MAG: **{stats.strength}/{stats.magic}**",
            f"DEX/SPD: **{stats.dex}/{stats.spd}**",
            f"DEF/RES: **{stats.defense}/{stats.res}**",
            f"LCK/BLD/MOV: **{stats.luck}/{stats.bld}/{stats.mov}**",
            f"Inventory: {inventory}",
        ]
    )


def build_preparation_embed(state: BattleState, deployed: Dict[str, str]) -> discord.Embed:
    enemies = ", ".join(sorted(unit.coord for unit in state.enemies.values()))
    deployed_lines: List[str] = []
    for slot in state.starting_positions:
        unit_name = deployed.get(slot, "Empty")
        deployed_lines.append(f"**{slot}** → {unit_name}")

    embed = discord.Embed(title="Preparation Phase", color=0x5C9E31)
    embed.description = "\n".join(
        [
            "Set your formation before battle begins.",
            "",
            f"### Enemy Units: **{len(state.enemies)}**",
            f"Enemy positions: {enemies}",
            "",
            f"### Player Start Spaces: {', '.join(state.starting_positions)}",
            "\n".join(deployed_lines),
            "",
            "### Win Condition: **Route the Enemy**",
            "### Loss Condition: **Lose All Units**",
        ]
    )
    embed.set_footer(text="Use Place Units, Inventory, Inspect, then Begin.")
    return embed


def build_inspect_embed(state: BattleState, coord: str) -> discord.Embed:
    terrain_name = str(terrain_info(state, coord)["name"])
    occupant_text = "No unit on this tile."
    for player in state.players.values():
        if player.coord == coord:
            occupant_text = f"Player unit on tile.\n\n{unit_info_block(player)}"
            break
    else:
        for enemy in state.enemies.values():
            if enemy.coord == coord:
                occupant_text = f"Enemy unit on tile.\n\n{unit_info_block(enemy)}"
                break

    embed = discord.Embed(title=f"Inspect {coord}", color=0x1D82B6)
    embed.description = "\n".join(
        [
            f"Terrain: **{terrain_name}**",
            "",
            occupant_text,
        ]
    )
    return embed


def build_movement_preview_embed(
    unit: Unit,
    *,
    preview_coord: str,
    steps_taken: int,
    movement_cap: int,
    support_range: bool,
) -> discord.Embed:
    action_color_text = "🟩 Green = ally-target range" if support_range else "🟥 Red = enemy-target range"
    embed = discord.Embed(title=f"Move: {unit.name}", color=0x1D82B6)
    embed.description = "\n".join(
        [
            f"Preview tile: **{preview_coord}**",
            f"Movement used: **{steps_taken}/{movement_cap}**",
            "Woods cost 2 movement and grant +30 avoid.",
            "",
            "🟦 Light blue = reachable movement tiles",
            action_color_text,
        ]
    )
    return embed


def build_inspect_range_embed(
    state: BattleState,
    coord: str,
    inspected_unit: Optional[Unit],
    supports_allies: bool = False,
) -> discord.Embed:
    embed = build_inspect_embed(state, coord)
    if inspected_unit is None:
        return embed
    range_label = "ally support range in green" if supports_allies else "attack range in red"
    embed.add_field(
        name="Range Overlay",
        value=(
            f"Showing **{inspected_unit.name}** movement range in blue and "
            f"{range_label}."
        ),
        inline=False,
    )
    return embed


def phase_banner_embed(phase: Literal["player", "enemy"]) -> discord.Embed:
    if phase == "player":
        return discord.Embed(title="PLAYER PHASE", color=0x1D82B6)
    return discord.Embed(title="ENEMY PHASE", color=0xC0392B)


def battle_result_embed(victory: bool) -> discord.Embed:
    if victory:
        return discord.Embed(title="VICTORY!", description="Win Condition Met: **Route the Enemy**", color=0x1D82B6)
    return discord.Embed(title="DEFEAT...", description="Loss Condition Met: **Lose All Units**", color=0xC0392B)


async def check_and_finalize_battle(interaction: discord.Interaction, state: BattleState) -> bool:
    if state.battle_over:
        return True

    victory = len(state.enemies) == 0
    defeat = len(state.players) == 0
    if not victory and not defeat:
        return False

    state.battle_over = True
    await lock_battle_message(interaction, state.active_battle_message_id)
    await interaction.channel.send(embed=battle_result_embed(victory))
    return True


async def refresh_battle_message(interaction: discord.Interaction, state: BattleState, message: discord.Message) -> None:
    img = render_battle_map(state)
    file = discord.File(img, filename="battle_map.png")
    embed = discord.Embed(title="Fire Emblem Mock Battle", description=state_summary(state), color=0x5C9E31)
    embed.set_image(url="attachment://battle_map.png")
    await message.edit(embed=embed, attachments=[file], view=BattleView(state, message.id))


async def lock_battle_message(interaction: discord.Interaction, message_id: Optional[int]) -> None:
    if message_id is None:
        return
    try:
        message = await interaction.channel.fetch_message(message_id)
    except discord.NotFound:
        return
    await message.edit(view=None)


async def create_phase_battle_message(interaction: discord.Interaction, state: BattleState) -> discord.Message:
    img = render_battle_map(state)
    file = discord.File(img, filename="battle_map.png")
    embed = discord.Embed(title="Fire Emblem Mock Battle", description=state_summary(state), color=0x5C9E31)
    embed.set_image(url="attachment://battle_map.png")
    message = await interaction.channel.send(embed=embed, file=file)
    await message.edit(view=BattleView(state, message.id))
    state.active_battle_message_id = message.id
    return message


def behavior_is_aggressive(state: BattleState, enemy: Unit) -> bool:
    if enemy.behavior == "aggressive":
        return True
    if enemy.behavior == "stationary":
        return False
    if enemy.name in state.provoked_enemies:
        return True
    if any(in_weapon_range(enemy, player) for player in state.players.values()):
        state.provoked_enemies.add(enemy.name)
        return True
    return False


def find_enemy_move_destination(state: BattleState, enemy_name: str) -> str:
    enemy = state.enemies[enemy_name]
    if not behavior_is_aggressive(state, enemy):
        return enemy.coord

    if not state.players:
        return enemy.coord

    current = enemy.coord
    blocked = occupied_coords(state, ignore_enemy=enemy_name)
    direction_priority = ["up", "left", "right", "down"]

    for _ in range(enemy.stats.mov):
        if any(
            enemy.equipped_weapon.rng_min <= manhattan_distance(current, player.coord) <= enemy.equipped_weapon.rng_max
            for player in state.players.values()
        ):
            break

        nearest_player_coord = min(state.players.values(), key=lambda player: manhattan_distance(current, player.coord)).coord
        next_coord = None
        next_distance = manhattan_distance(current, nearest_player_coord)
        for direction in direction_priority:
            candidate = move_coord(current, direction)
            if candidate == current or candidate in blocked:
                continue
            candidate_distance = manhattan_distance(candidate, nearest_player_coord)
            if candidate_distance < next_distance:
                next_coord = candidate
                next_distance = candidate_distance

        if next_coord is None:
            break

        current = next_coord
        blocked.add(current)
    return current


def apply_on_hit_effects(attacker: Unit, defender: Unit, *, lines: List[str]) -> None:
    if attacker.equipped_weapon.inflicts_poison:
        defender.poison_stacks = clamp(defender.poison_stacks + 1, 0, 3)
        lines.append(f"☠️ {defender.name} is poisoned (stacks: {defender.poison_stacks}).")


def apply_after_combat_skill(attacker: Unit, defender: Unit, *, lines: List[str]) -> None:
    # Framme personal: if attacker used a tome, poison target after combat.
    if attacker.name == "Framme" and attacker.equipped_weapon.kind == "tome" and defender.current_hp > 0:
        defender.poison_stacks = clamp(defender.poison_stacks + 1, 0, 3)
        lines.append(f"✨ Toxic Tome activates: {defender.name} is poisoned (stacks: {defender.poison_stacks}).")


@dataclass
class CriticalEvent:
    attacker: Unit
    quote: str


def resolve_combat_round(attacker: Unit, defender: Unit, lines: List[str], critical_events: List[CriticalEvent]) -> bool:
    hit = calc_hit(attacker, defender)
    crit = calc_crit(attacker, defender)
    if random.randint(1, 100) > hit:
        lines.append(f"{attacker.name} attacks with {attacker.equipped_weapon.name}, but misses.")
        return defender.current_hp <= 0

    dmg = calc_damage(attacker, defender)
    critted = random.randint(1, 100) <= crit
    total = dmg * 3 if critted else dmg
    if critted:
        critical_index = len(critical_events)
        critical_events.append(CriticalEvent(attacker=attacker, quote=random_critical_quote(attacker)))
        lines.append(f"[CRITICAL_EVENT:{critical_index}]")
    defender.current_hp = max(0, defender.current_hp - total)
    lines.append(
        f"{attacker.name} hits {defender.name} for **{total}** damage. "
        f"({defender.current_hp}/{defender.stats.hp} HP left)"
    )
    apply_on_hit_effects(attacker, defender, lines=lines)
    return defender.current_hp <= 0


def heal_amount(healer: Unit) -> int:
    return healer.equipped_weapon.heal_power + healer.stats.magic


async def run_enemy_phase(interaction: discord.Interaction, state: BattleState, battle_message: discord.Message) -> None:
    state.phase = "enemy"
    state.moved_this_turn.clear()
    await interaction.channel.send(embed=phase_banner_embed("enemy"))
    await lock_battle_message(interaction, battle_message.id)
    active_enemy_message = await create_phase_battle_message(interaction, state)

    for enemy_name in list(state.enemies.keys()):
        if enemy_name not in state.enemies or not state.players or state.battle_over:
            continue
        destination = find_enemy_move_destination(state, enemy_name)
        enemy = state.enemies[enemy_name]
        enemy.coord = destination
        state.moved_this_turn.add(enemy_name)
        await refresh_battle_message(interaction, state, active_enemy_message)
        await asyncio.sleep(0.4)

        targets = [player for player in state.players.values() if in_weapon_range(enemy, player)]
        if not targets:
            continue

        target = min(targets, key=lambda player: (player.current_hp, manhattan_distance(enemy.coord, player.coord)))
        lines: List[str] = [f"**{enemy.name}** initiates combat against **{target.name}**!"]
        critical_events: List[CriticalEvent] = []
        player_pre_hp = target.current_hp
        enemy_pre_hp = enemy.current_hp
        defender_down = resolve_combat_round(enemy, target, lines, critical_events)
        if defender_down:
            lines.append(f"💀 {target.name} is defeated!")
            state.players.pop(target.name, None)
            state.moved_this_turn.discard(target.name)
        elif in_weapon_range(target, enemy):
            attacker_down = resolve_combat_round(target, enemy, lines, critical_events)
            apply_after_combat_skill(target, enemy, lines=lines)
            if attacker_down:
                lines.append(f"💀 {enemy.name} is defeated!")
                state.enemies.pop(enemy.name, None)

        await send_action_log_sequence(
            interaction,
            enemy,
            target,
            lines,
            critical_events,
            player_pre_hp=player_pre_hp,
            enemy_pre_hp=enemy_pre_hp,
        )
        await refresh_battle_message(interaction, state, active_enemy_message)
        if await check_and_finalize_battle(interaction, state):
            return
        await asyncio.sleep(0.4)

    if await check_and_finalize_battle(interaction, state):
        return

    state.phase = "player"
    state.moved_this_turn.clear()
    await interaction.channel.send(embed=phase_banner_embed("player"))
    await lock_battle_message(interaction, active_enemy_message.id)
    await create_phase_battle_message(interaction, state)


def build_prebattle_embed(player: Unit, enemy: Unit) -> discord.Embed:
    player_dmg = calc_damage(player, enemy)
    player_hit = calc_hit(player, enemy)
    player_crit = calc_crit(player, enemy)
    enemy_can_counter = in_weapon_range(enemy, player)
    enemy_dmg = calc_damage(enemy, player) if enemy_can_counter else 0
    enemy_hit = calc_hit(enemy, player) if enemy_can_counter else 0
    enemy_crit = calc_crit(enemy, player) if enemy_can_counter else 0
    player_after_hp = max(0, player.current_hp - enemy_dmg)
    enemy_after_hp = max(0, enemy.current_hp - player_dmg)

    embed = discord.Embed(title="Combat Forecast", color=0x1D82B6)
    embed.add_field(
        name=f"⚔️ {player.name} (Player)",
        value="\n".join([
            f"Weapon: **{player.equipped_weapon.name}**",
            f"HP: **{player_after_hp}/{player.stats.hp}** {hp_bar(player_after_hp, player.stats.hp)}",
            "",
            f"Dmg: **{player_dmg}**",
            f"Hit: **{player_hit}%**",
            f"Crit: **{player_crit}%**",
        ]),
        inline=True,
    )
    embed.add_field(
        name=f"🛡️ {enemy.name} (Enemy)",
        value="\n".join([
            f"Weapon: **{enemy.equipped_weapon.name}**",
            f"HP: **{enemy_after_hp}/{enemy.stats.hp}** {hp_bar(enemy_after_hp, enemy.stats.hp, fill_block='🟥')}",
            "",
            f"Dmg: **{enemy_dmg}**",
            f"Hit: **{enemy_hit}%**",
            f"Crit: **{enemy_crit}%**",
        ]),
        inline=True,
    )
    embed.set_footer(text="Fight to commit action, Weapon to swap, Back to undo movement.")
    return embed


def build_heal_forecast_embed(healer: Unit, ally: Unit) -> discord.Embed:
    amount = heal_amount(healer)
    healed_hp = min(ally.stats.hp, ally.current_hp + amount)
    recovered = healed_hp - ally.current_hp
    embed = discord.Embed(title="Healing Forecast", color=0x1D82B6)
    embed.add_field(
        name=f"✨ {healer.name} (Healer)",
        value="\n".join([
            f"Staff: **{healer.equipped_weapon.name}**",
            f"Heal Power: **{amount}**",
        ]),
        inline=True,
    )
    embed.add_field(
        name=f"🩹 {ally.name} (Ally)",
        value="\n".join([
            f"HP: **{ally.current_hp}/{ally.stats.hp}** → **{healed_hp}/{ally.stats.hp}**",
            f"Recovered: **{recovered} HP**",
        ]),
        inline=True,
    )
    return embed


def build_action_log_embed(lines: List[str], initiator: Unit) -> discord.Embed:
    embed = discord.Embed(title="Action Log", description="\n".join(lines), color=0xD67F2C)
    initiator_image = resolve_asset_for_unit(initiator)
    if initiator_image is not None:
        filename = os.path.basename(initiator_image)
        embed.set_thumbnail(url=f"attachment://{filename}")
    return embed


def build_critical_embed(event: CriticalEvent) -> discord.Embed:
    embed = discord.Embed(
        title="CRITICAL!",
        description=f"***“{event.quote}”***",
        color=0x9B59B6,
    )
    profile = profile_for_unit(event.attacker)
    if profile.critical_image_url:
        embed.set_image(url=profile.critical_image_url)
        return embed
    critical_image = resolve_asset_for_unit(event.attacker, use_critical_image=True)
    if critical_image is not None:
        filename = os.path.basename(critical_image)
        embed.set_image(url=f"attachment://{filename}")
    return embed


def build_battle_scene_embed(
    attacker: Unit,
    defender: Unit,
    *,
    player_hp_override: Optional[int] = None,
    enemy_hp_override: Optional[int] = None,
) -> discord.Embed:
    player_unit = attacker if attacker.name in PLAYER_UNITS_BY_NAME else defender
    enemy_unit = defender if player_unit is attacker else attacker
    player_hp = player_unit.current_hp if player_hp_override is None else player_hp_override
    enemy_hp = enemy_unit.current_hp if enemy_hp_override is None else enemy_hp_override
    embed = discord.Embed(title="Battle Scene", color=0x1D82B6)
    embed.add_field(
        name=f"⚔️ {player_unit.name} (Player)",
        value="\n".join([
            f"Weapon: **{player_unit.equipped_weapon.name}**",
            f"HP: **{player_hp}/{player_unit.stats.hp}** {hp_bar(player_hp, player_unit.stats.hp)}",
            "",
            f"Dmg: **{calc_damage(player_unit, enemy_unit)}**",
            f"Hit: **{calc_hit(player_unit, enemy_unit)}%**",
            f"Crit: **{calc_crit(player_unit, enemy_unit)}%**",
        ]),
        inline=True,
    )
    embed.add_field(
        name=f"🛡️ {enemy_unit.name} (Enemy)",
        value="\n".join([
            f"Weapon: **{enemy_unit.equipped_weapon.name}**",
            f"HP: **{enemy_hp}/{enemy_unit.stats.hp}** {hp_bar(enemy_hp, enemy_unit.stats.hp, fill_block='🟥')}",
            "",
            f"Dmg: **{calc_damage(enemy_unit, player_unit)}**",
            f"Hit: **{calc_hit(enemy_unit, player_unit)}%**",
            f"Crit: **{calc_crit(enemy_unit, player_unit)}%**",
        ]),
        inline=True,
    )
    embed.set_image(url="attachment://battle_scene.png")
    return embed


async def send_embed_with_unit_asset(
    channel: discord.abc.Messageable,
    embed: discord.Embed,
    unit: Unit,
    *,
    use_critical_image: bool = False,
) -> None:
    # If the embed already points at a remote image URL (e.g. a critical splash art),
    # do not attach a fallback unit file. Discord will render that extra file as a
    # separate non-embed image message.
    embed_image_url = embed.image.url if embed.image else None
    if embed_image_url and not embed_image_url.startswith("attachment://"):
        await channel.send(embed=embed)
        return
    asset_path = resolve_asset_for_unit(unit, use_critical_image=use_critical_image)
    if asset_path is not None:
        file = discord.File(asset_path, filename=os.path.basename(asset_path))
        await channel.send(embed=embed, file=file)
        return
    await channel.send(embed=embed)


class BattleSceneContinueView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self._continue = asyncio.Event()

    @discord.ui.button(label="Go", style=discord.ButtonStyle.success)
    async def go(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        button.disabled = True
        await interaction.response.edit_message(view=self)
        self._continue.set()

    async def wait_for_continue(self) -> None:
        await self._continue.wait()


async def send_action_log_sequence(
    interaction: discord.Interaction,
    attacker: Unit,
    defender: Unit,
    lines: List[str],
    critical_events: List[CriticalEvent],
    *,
    player_pre_hp: Optional[int] = None,
    enemy_pre_hp: Optional[int] = None,
) -> None:
    player_unit = attacker if attacker.name in PLAYER_UNITS_BY_NAME else defender
    enemy_unit = defender if player_unit is attacker else attacker
    scene_player_hp = player_unit.current_hp if player_pre_hp is None else player_pre_hp
    scene_enemy_hp = enemy_unit.current_hp if enemy_pre_hp is None else enemy_pre_hp

    battle_scene = render_battle_scene(attacker, defender)
    if battle_scene is not None:
        continue_view = BattleSceneContinueView()
        await interaction.channel.send(
            embed=build_battle_scene_embed(
                attacker,
                defender,
                player_hp_override=scene_player_hp,
                enemy_hp_override=scene_enemy_hp,
            ),
            file=discord.File(battle_scene, filename="battle_scene.png"),
            view=continue_view,
        )
        await continue_view.wait_for_continue()

    if not critical_events:
        await send_embed_with_unit_asset(interaction.channel, build_action_log_embed(lines, attacker), attacker)
        return

    buffered: List[str] = []
    for line in lines:
        if line.startswith("[CRITICAL_EVENT:") and line.endswith("]"):
            if buffered:
                await send_embed_with_unit_asset(interaction.channel, build_action_log_embed(buffered, attacker), attacker)
                buffered = []
            marker = line.removeprefix("[CRITICAL_EVENT:").removesuffix("]")
            if marker.isdigit():
                idx = int(marker)
                if 0 <= idx < len(critical_events):
                    event = critical_events[idx]
                    await send_embed_with_unit_asset(
                        interaction.channel,
                        build_critical_embed(event),
                        event.attacker,
                        use_critical_image=True,
                    )
            continue
        buffered.append(line)

    if buffered:
        await send_embed_with_unit_asset(interaction.channel, build_action_log_embed(buffered, attacker), attacker)


async def send_single_action_log(
    interaction: discord.Interaction,
    initiator: Unit,
    lines: List[str],
) -> None:
    await send_embed_with_unit_asset(
        interaction.channel,
        build_action_log_embed(lines, initiator),
        initiator,
    )


class HealTargetSelect(discord.ui.Select):
    def __init__(self, state: BattleState, healer_name: str):
        healer = state.players[healer_name]
        options: List[discord.SelectOption] = []
        for ally in state.players.values():
            if ally.name == healer.name:
                continue
            if ally.current_hp >= ally.stats.hp:
                continue
            if in_weapon_range(healer, ally):
                options.append(discord.SelectOption(label=ally.name, value=ally.name))
        super().__init__(placeholder="Choose ally to heal", options=options)
        self.state = state
        self.healer_name = healer_name

    async def callback(self, interaction: discord.Interaction) -> None:
        ally_name = self.values[0]
        await interaction.response.edit_message(
            content=f"Selected {ally_name} for healing.",
            embed=build_heal_forecast_embed(self.state.players[self.healer_name], self.state.players[ally_name]),
            view=HealForecastView(self.state, self.healer_name, ally_name),
        )


class HealTargetView(discord.ui.View):
    def __init__(self, state: BattleState, healer_name: str):
        super().__init__(timeout=180)
        self.add_item(HealTargetSelect(state, healer_name))


class HealForecastView(discord.ui.View):
    def __init__(self, state: BattleState, healer_name: str, ally_name: str):
        super().__init__(timeout=180)
        self.state = state
        self.healer_name = healer_name
        self.ally_name = ally_name

    @discord.ui.button(label="Heal", style=discord.ButtonStyle.success)
    async def heal(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        healer = self.state.players[self.healer_name]
        ally = self.state.players[self.ally_name]
        amount = heal_amount(healer)
        before = ally.current_hp
        ally.current_hp = min(ally.stats.hp, ally.current_hp + amount)
        recovered = ally.current_hp - before
        self.state.moved_this_turn.add(self.healer_name)
        await interaction.response.edit_message(
            content=f"{healer.name} uses {healer.equipped_weapon.name} on {ally.name}: {before} → {ally.current_hp} HP.",
            embed=None,
            view=None,
        )
        action_lines = [
            f"**{healer.name}** uses **{healer.equipped_weapon.name}** on **{ally.name}**.",
            f"🩹 {ally.name} recovers **{recovered} HP** ({before} → {ally.current_hp}).",
        ]
        await send_single_action_log(interaction, healer, action_lines)
        active_message_id = self.state.active_battle_message_id
        if active_message_id is not None:
            battle_message = await interaction.channel.fetch_message(active_message_id)
            await refresh_battle_message(interaction, self.state, battle_message)
        if self.state.phase == "player" and len(self.state.moved_this_turn) >= len(self.state.players):
            await interaction.followup.send("All remaining player units acted. Ending Player Phase.", ephemeral=True)
            if active_message_id is not None:
                battle_message = await interaction.channel.fetch_message(active_message_id)
                await run_enemy_phase(interaction, self.state, battle_message)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Choose an ally to heal.",
            embed=None,
            view=HealTargetView(self.state, self.healer_name),
        )


class WeaponSelect(discord.ui.Select):
    def __init__(self, state: BattleState, player_name: str, enemy_name: str, start_coord: str):
        player = state.players[player_name]
        options = [discord.SelectOption(label=weapon.name, value=str(idx)) for idx, weapon in enumerate(player.inventory)]
        super().__init__(placeholder="Choose weapon", options=options)
        self.state = state
        self.player_name = player_name
        self.enemy_name = enemy_name
        self.start_coord = start_coord

    async def callback(self, interaction: discord.Interaction) -> None:
        player = self.state.players[self.player_name]
        player.equipped_index = int(self.values[0])
        enemy = self.state.enemies[self.enemy_name]
        embed = build_prebattle_embed(player, enemy)
        await interaction.response.edit_message(embed=embed, view=PreBattleView(self.state, self.player_name, self.enemy_name, self.start_coord))


class WeaponSelectView(discord.ui.View):
    def __init__(self, state: BattleState, player_name: str, enemy_name: str, start_coord: str):
        super().__init__(timeout=180)
        self.add_item(WeaponSelect(state, player_name, enemy_name, start_coord))


class AttackTargetSelect(discord.ui.Select):
    def __init__(self, state: BattleState, player_name: str, start_coord: str):
        player = state.players[player_name]
        options: List[discord.SelectOption] = []
        for enemy in state.enemies.values():
            if in_weapon_range(player, enemy):
                options.append(discord.SelectOption(label=enemy.name, value=enemy.name))
        super().__init__(placeholder="Choose enemy to attack", options=options)
        self.state = state
        self.player_name = player_name
        self.start_coord = start_coord

    async def callback(self, interaction: discord.Interaction) -> None:
        enemy_name = self.values[0]
        player = self.state.players[self.player_name]
        enemy = self.state.enemies[enemy_name]
        await interaction.response.edit_message(
            content=f"Selected target: **{enemy_name}**",
            embed=build_prebattle_embed(player, enemy),
            view=PreBattleView(self.state, self.player_name, enemy_name, self.start_coord),
        )


class AttackTargetView(discord.ui.View):
    def __init__(self, state: BattleState, player_name: str, start_coord: str):
        super().__init__(timeout=180)
        self.add_item(AttackTargetSelect(state, player_name, start_coord))


class EnemyActionView(discord.ui.View):
    def __init__(self, state: BattleState, unit_name: str, start_coord: str):
        super().__init__(timeout=240)
        self.state = state
        self.unit_name = unit_name
        self.start_coord = start_coord

    @discord.ui.button(label="Fight", style=discord.ButtonStyle.danger)
    async def fight(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        player = self.state.players[self.unit_name]
        in_range_enemies = [enemy for enemy in self.state.enemies.values() if in_weapon_range(player, enemy)]
        if not in_range_enemies:
            await interaction.response.send_message("No enemies are currently in range.", ephemeral=True)
            return

        target_names = ", ".join(enemy.name for enemy in in_range_enemies)
        await interaction.response.edit_message(
            content=f"Enemies in range: **{target_names}**. Pick a target to view combat forecast.",
            embed=None,
            view=AttackTargetView(self.state, self.unit_name, self.start_coord),
        )

    @discord.ui.button(label="Items", style=discord.ButtonStyle.primary)
    async def items(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        unit = self.state.players[self.unit_name]
        await interaction.response.edit_message(
            content=f"Choose inventory for **{unit.name}** (equip weapon; usable items coming soon).",
            embed=None,
            view=PostMoveInventoryView(self.state, self.unit_name, self.start_coord, return_with_fight=True),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        player = self.state.players.get(self.unit_name)
        if player is not None:
            player.coord = self.start_coord
        await interaction.response.edit_message(content="Movement undone. Reposition your unit.", embed=None, view=None)
        active_message_id = self.state.active_battle_message_id
        if active_message_id is not None:
            battle_message = await interaction.channel.fetch_message(active_message_id)
            await refresh_battle_message(interaction, self.state, battle_message)

    @discord.ui.button(label="Wait", style=discord.ButtonStyle.success)
    async def wait(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.state.moved_this_turn.add(self.unit_name)
        await interaction.response.edit_message(
            content=f"**{self.unit_name}** waits and ends their turn.",
            embed=None,
            view=None,
        )
        active_message_id = self.state.active_battle_message_id
        if active_message_id is not None:
            battle_message = await interaction.channel.fetch_message(active_message_id)
            await refresh_battle_message(interaction, self.state, battle_message)
        if self.state.phase == "player" and len(self.state.moved_this_turn) >= len(self.state.players):
            await interaction.followup.send("All remaining player units acted. Ending Player Phase.", ephemeral=True)
            if active_message_id is not None:
                battle_message = await interaction.channel.fetch_message(active_message_id)
                await run_enemy_phase(interaction, self.state, battle_message)


class PreBattleView(discord.ui.View):
    def __init__(self, state: BattleState, player_name: str, enemy_name: str, start_coord: str):
        super().__init__(timeout=300)
        self.state = state
        self.player_name = player_name
        self.enemy_name = enemy_name
        self.start_coord = start_coord

    @discord.ui.button(label="Fight", style=discord.ButtonStyle.danger)
    async def fight(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.state.battle_over:
            await interaction.response.send_message("This battle is already over.", ephemeral=True)
            return
        player = self.state.players[self.player_name]
        enemy = self.state.enemies[self.enemy_name]
        lines: List[str] = [f"**{player.name}** initiates combat against **{enemy.name}**!"]
        critical_events: List[CriticalEvent] = []
        player_pre_hp = player.current_hp
        enemy_pre_hp = enemy.current_hp

        defender_down = resolve_combat_round(player, enemy, lines, critical_events)
        if defender_down:
            lines.append(f"💀 {enemy.name} is defeated!")
            self.state.enemies.pop(enemy.name, None)
        elif in_weapon_range(enemy, player):
            attacker_down = resolve_combat_round(enemy, player, lines, critical_events)
            if attacker_down:
                lines.append(f"💀 {player.name} is defeated!")
                self.state.players.pop(player.name, None)
        else:
            lines.append(f"{enemy.name} cannot counterattack (out of range).")

        apply_after_combat_skill(player, enemy, lines=lines)
        self.state.moved_this_turn.add(self.player_name)
        await interaction.response.edit_message(content="Combat resolved.", embed=None, view=None)

        await send_action_log_sequence(
            interaction,
            player,
            enemy,
            lines,
            critical_events,
            player_pre_hp=player_pre_hp,
            enemy_pre_hp=enemy_pre_hp,
        )

        active_message_id = self.state.active_battle_message_id
        if active_message_id is not None:
            battle_message = await interaction.channel.fetch_message(active_message_id)
            await refresh_battle_message(interaction, self.state, battle_message)
        if await check_and_finalize_battle(interaction, self.state):
            return

        if self.state.phase == "player" and len(self.state.moved_this_turn) >= len(self.state.players):
            await interaction.followup.send("All remaining player units acted. Ending Player Phase.", ephemeral=True)
            if active_message_id is not None:
                battle_message = await interaction.channel.fetch_message(active_message_id)
                await run_enemy_phase(interaction, self.state, battle_message)

    @discord.ui.button(label="Weapon", style=discord.ButtonStyle.primary)
    async def weapon(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Select a weapon to equip:",
            embed=None,
            view=WeaponSelectView(self.state, self.player_name, self.enemy_name, self.start_coord),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        player = self.state.players.get(self.player_name)
        if player is not None:
            player.coord = self.start_coord
        await interaction.response.edit_message(content="Movement undone. Reposition your unit.", embed=None, view=None)
        active_message_id = self.state.active_battle_message_id
        if active_message_id is not None:
            battle_message = await interaction.channel.fetch_message(active_message_id)
            await refresh_battle_message(interaction, self.state, battle_message)


class DirectionView(discord.ui.View):
    def __init__(self, state: BattleState, unit_name: str, battle_message_id: int):
        super().__init__(timeout=300)
        self.state = state
        self.unit_name = unit_name
        self.battle_message_id = battle_message_id
        self.start_coord = state.players[unit_name].coord
        self.preview_coord = self.start_coord
        self.steps_taken = 0
        self.path: List[str] = [self.start_coord]

    @property
    def movement_cap(self) -> int:
        return self.state.players[self.unit_name].stats.mov

    def preview_file_and_embed(self) -> Tuple[discord.File, discord.Embed]:
        unit = self.state.players[self.unit_name]
        move_tiles, action_tiles, is_support = movement_and_action_ranges(self.state, unit)
        action_color = (22, 163, 74, 100) if is_support else (220, 38, 38, 100)
        action_outline = (21, 128, 61, 255) if is_support else (153, 27, 27, 255)

        # Keep movement range based on the unit's true origin tile, but render the
        # marker at the live preview tile so the private movement embed updates as
        # the player steps around.
        original_coord = unit.coord
        unit.coord = self.preview_coord
        try:
            allies_in_range = {
                ally.coord
                for ally in self.state.players.values()
                if ally.name != unit.name and in_weapon_range(unit, ally)
            }
            img = render_battle_map(
                self.state,
                highlight_move_coords=move_tiles,
                highlight_action_coords=action_tiles,
                highlight_ally_coords=allies_in_range,
                action_color=action_color,
                action_outline=action_outline,
            )
        finally:
            unit.coord = original_coord
        file = discord.File(img, filename="movement_preview.png")
        embed = build_movement_preview_embed(
            unit,
            preview_coord=self.preview_coord,
            steps_taken=self.steps_taken,
            movement_cap=self.movement_cap,
            support_range=is_support,
        )
        embed.set_image(url="attachment://movement_preview.png")
        return file, embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    async def _safe_edit_message(self, interaction: discord.Interaction, **kwargs: object) -> None:
        try:
            await interaction.response.edit_message(**kwargs)
        except discord.NotFound as exc:
            if getattr(exc, "code", None) == 10062:
                logger.warning(
                    "Skipping stale Discord interaction while updating DirectionView for %s.",
                    self.unit_name,
                )
                return
            raise

    async def _shift(self, interaction: discord.Interaction, direction: str) -> None:
        if self.steps_taken >= self.movement_cap:
            await interaction.response.send_message(
                f"{self.unit_name} has reached max movement ({self.movement_cap}). Confirm or keep current tile.",
                ephemeral=True,
            )
            return

        candidate = move_coord(self.preview_coord, direction)
        if candidate == self.preview_coord:
            await interaction.response.send_message("That tile is blocked.", ephemeral=True)
            return

        if not is_infantry_passable(self.state, candidate):
            await interaction.response.send_message("Infantry cannot move onto that terrain.", ephemeral=True)
            return

        move_cost = terrain_movement_cost(self.state, candidate)
        new_steps = self.steps_taken + move_cost
        if new_steps > self.movement_cap:
            await interaction.response.send_message(
                f"That move costs {move_cost} movement and exceeds {self.unit_name}'s MOV.",
                ephemeral=True,
            )
            return

        self.preview_coord = candidate
        self.steps_taken = new_steps
        self.path.append(candidate)
        file, embed = self.preview_file_and_embed()
        await self._safe_edit_message(
            interaction,
            content=f"Moving {self.unit_name}. Use direction buttons then Confirm.",
            embed=embed,
            attachments=[file],
            view=self,
        )

    @discord.ui.button(label="Left", style=discord.ButtonStyle.secondary)
    async def left(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._shift(interaction, "left")

    @discord.ui.button(label="Up", style=discord.ButtonStyle.secondary)
    async def up(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._shift(interaction, "up")

    @discord.ui.button(label="Right", style=discord.ButtonStyle.secondary)
    async def right(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._shift(interaction, "right")

    @discord.ui.button(label="Down", style=discord.ButtonStyle.secondary)
    async def down(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._shift(interaction, "down")

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back_step(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if len(self.path) <= 1:
            await interaction.response.send_message("No previous movement step to reverse.", ephemeral=True)
            return
        self.path.pop()
        self.preview_coord = self.path[-1]
        self.steps_taken = sum(terrain_movement_cost(self.state, tile) for tile in self.path[1:])
        file, embed = self.preview_file_and_embed()
        await self._safe_edit_message(
            interaction,
            content=f"Moving {self.unit_name}. Use direction buttons then Confirm.",
            embed=embed,
            attachments=[file],
            view=self,
        )

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.secondary)
    async def reset_path(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.preview_coord = self.start_coord
        self.steps_taken = 0
        self.path = [self.start_coord]
        file, embed = self.preview_file_and_embed()
        await self._safe_edit_message(
            interaction,
            content=f"Moving {self.unit_name}. Use direction buttons then Confirm.",
            embed=embed,
            attachments=[file],
            view=self,
        )

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        blocked = occupied_coords(self.state, ignore_player=self.unit_name)
        if self.preview_coord in blocked:
            await interaction.response.send_message(
                "You can't end movement on an occupied tile.",
                ephemeral=True,
            )
            return

        if not is_infantry_passable(self.state, self.preview_coord):
            await interaction.response.send_message("You cannot end movement on impassable terrain.", ephemeral=True)
            return

        self.state.players[self.unit_name].coord = self.preview_coord
        await self._safe_edit_message(
            interaction,
            content=f"Confirmed {self.unit_name} to `{self.preview_coord}`.",
            view=None,
        )
        active_message_id = self.state.active_battle_message_id
        if active_message_id is None:
            await interaction.followup.send("Unable to refresh battle map message.", ephemeral=True)
            return
        battle_message = await interaction.channel.fetch_message(active_message_id)
        await refresh_battle_message(interaction, self.state, battle_message)

        player = self.state.players[self.unit_name]
        if player.equipped_weapon.kind == "staff":
            heal_targets = [ally for ally in self.state.players.values() if ally.name != player.name and ally.current_hp < ally.stats.hp and in_weapon_range(player, ally)]
            if heal_targets:
                target_names = ", ".join(ally.name for ally in heal_targets)
                await interaction.followup.send(
                    f"Allies in staff range: **{target_names}**. Pick a target to view healing forecast.",
                    view=HealTargetView(self.state, self.unit_name),
                    ephemeral=True,
                )
                return
            await interaction.followup.send("No injured ally in staff range. You can still End phase.", ephemeral=True)
            return

        in_range_enemies = [enemy for enemy in self.state.enemies.values() if in_weapon_range(player, enemy)]
        if in_range_enemies:
            await interaction.followup.send(
                f"**{player.name}** can act from here.",
                view=EnemyActionView(self.state, self.unit_name, self.start_coord),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"**{player.name}** will move here.",
            view=NoEnemyActionView(self.state, self.unit_name, self.start_coord),
            ephemeral=True,
        )


class PostMoveInventorySelect(discord.ui.Select):
    def __init__(self, state: BattleState, unit_name: str, start_coord: str, return_with_fight: bool):
        unit = state.players[unit_name]
        options: List[discord.SelectOption] = []
        for idx, weapon in enumerate(unit.inventory):
            equipped = " (Equipped)" if idx == unit.equipped_index else ""
            options.append(discord.SelectOption(label=f"Equip: {weapon.name}{equipped}", value=str(idx)))
        super().__init__(placeholder="Choose gear to equip", options=options)
        self.state = state
        self.unit_name = unit_name
        self.start_coord = start_coord
        self.return_with_fight = return_with_fight

    async def callback(self, interaction: discord.Interaction) -> None:
        unit = self.state.players[self.unit_name]
        new_index = int(self.values[0])
        unit.equipped_index = new_index
        await interaction.response.edit_message(
            content=f"Equipped **{unit.inventory[new_index].name}** on **{unit.name}**.",
            view=PostMoveInventoryView(self.state, self.unit_name, self.start_coord, return_with_fight=self.return_with_fight),
        )


class PostMoveInventoryView(discord.ui.View):
    def __init__(self, state: BattleState, unit_name: str, start_coord: str, *, return_with_fight: bool = False):
        super().__init__(timeout=180)
        self.state = state
        self.unit_name = unit_name
        self.start_coord = start_coord
        self.return_with_fight = return_with_fight
        self.add_item(PostMoveInventorySelect(state, unit_name, start_coord, return_with_fight))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        unit = self.state.players[self.unit_name]
        next_view: discord.ui.View
        if self.return_with_fight:
            next_view = EnemyActionView(self.state, self.unit_name, self.start_coord)
        else:
            next_view = NoEnemyActionView(self.state, self.unit_name, self.start_coord)
        await interaction.response.edit_message(
            content=f"**{unit.name}** will move here.",
            view=next_view,
        )


class NoEnemyActionView(discord.ui.View):
    def __init__(self, state: BattleState, unit_name: str, start_coord: str):
        super().__init__(timeout=240)
        self.state = state
        self.unit_name = unit_name
        self.start_coord = start_coord

    @discord.ui.button(label="Wait", style=discord.ButtonStyle.success)
    async def wait(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.state.moved_this_turn.add(self.unit_name)
        await interaction.response.edit_message(
            content=f"**{self.unit_name}** waits and ends their turn.",
            embed=None,
            view=None,
        )
        active_message_id = self.state.active_battle_message_id
        if active_message_id is not None:
            battle_message = await interaction.channel.fetch_message(active_message_id)
            await refresh_battle_message(interaction, self.state, battle_message)
        if self.state.phase == "player" and len(self.state.moved_this_turn) >= len(self.state.players):
            await interaction.followup.send("All remaining player units acted. Ending Player Phase.", ephemeral=True)
            if active_message_id is not None:
                battle_message = await interaction.channel.fetch_message(active_message_id)
                await run_enemy_phase(interaction, self.state, battle_message)

    @discord.ui.button(label="Items", style=discord.ButtonStyle.primary)
    async def items(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        unit = self.state.players[self.unit_name]
        await interaction.response.edit_message(
            content=f"Choose inventory for **{unit.name}** (equip weapon; usable items coming soon).",
            embed=None,
            view=PostMoveInventoryView(self.state, self.unit_name, self.start_coord),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        player = self.state.players.get(self.unit_name)
        if player is not None:
            player.coord = self.start_coord
        await interaction.response.edit_message(content="Movement undone. Reposition your unit.", embed=None, view=None)
        active_message_id = self.state.active_battle_message_id
        if active_message_id is not None:
            battle_message = await interaction.channel.fetch_message(active_message_id)
            await refresh_battle_message(interaction, self.state, battle_message)


class PickUnitView(discord.ui.View):
    def __init__(self, state: BattleState, battle_message_id: int):
        super().__init__(timeout=300)
        self.state = state
        self.battle_message_id = battle_message_id

        available = [u.name for u in state.players.values() if u.name not in state.moved_this_turn]
        options = [discord.SelectOption(label=name, value=name) for name in available]
        self.add_item(UnitSelect(options, state, battle_message_id))


class UnitSelect(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption], state: BattleState, battle_message_id: int):
        super().__init__(placeholder="Choose a unit to move", options=options)
        self.state = state
        self.battle_message_id = battle_message_id

    async def callback(self, interaction: discord.Interaction) -> None:
        # Acknowledge immediately so expensive map preview rendering cannot expire
        # the interaction token (Discord 404/10062 Unknown interaction).
        await interaction.response.defer()

        unit_name = self.values[0]
        view = DirectionView(self.state, unit_name, self.battle_message_id)
        file, embed = view.preview_file_and_embed()
        await interaction.edit_original_response(
            content=f"Moving {unit_name}. Use direction buttons then Confirm.",
            embed=embed,
            attachments=[file],
            view=view,
        )


class BattleView(discord.ui.View):
    def __init__(self, state: BattleState, battle_message_id: int):
        super().__init__(timeout=None)
        self.state = state
        self.battle_message_id = battle_message_id

    @discord.ui.button(label="Move", style=discord.ButtonStyle.primary)
    async def move(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.state.battle_over:
            await interaction.response.send_message("This battle is already over.", ephemeral=True)
            return
        if self.state.phase != "player":
            await interaction.response.send_message("You can only move units during Player Phase.", ephemeral=True)
            return
        available = [u.name for u in self.state.players.values() if u.name not in self.state.moved_this_turn]
        if not available:
            await interaction.response.send_message("All player units have already acted this phase.", ephemeral=True)
            return

        picker = PickUnitView(self.state, self.battle_message_id)
        await interaction.response.send_message("Pick a unit to move:", view=picker, ephemeral=True)

    @discord.ui.button(label="Inspect", style=discord.ButtonStyle.secondary)
    async def inspect(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.state.battle_over:
            await interaction.response.send_message("This battle is already over.", ephemeral=True)
            return
        await interaction.response.send_modal(InspectCoordinateModal(self.state))

    @discord.ui.button(label="End", style=discord.ButtonStyle.danger)
    async def end_phase(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if self.state.battle_over:
            await interaction.response.send_message("This battle is already over.", ephemeral=True)
            return
        if self.state.phase != "player":
            await interaction.response.send_message("You can only end phase during Player Phase.", ephemeral=True)
            return
        await interaction.response.send_message(
            "End Player Phase now?",
            view=EndPhaseConfirmView(self.state, self.battle_message_id),
            ephemeral=True,
        )


class EndPhaseConfirmView(discord.ui.View):
    def __init__(self, state: BattleState, battle_message_id: int):
        super().__init__(timeout=120)
        self.state = state
        self.battle_message_id = battle_message_id

    @discord.ui.button(label="Confirm End", style=discord.ButtonStyle.danger)
    async def confirm_end(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        active_message_id = self.state.active_battle_message_id
        if active_message_id is None:
            await interaction.response.edit_message(content="No active battle message found.", view=None)
            return
        battle_message = await interaction.channel.fetch_message(active_message_id)
        await interaction.response.edit_message(content="Ending Player Phase.", view=None)
        await run_enemy_phase(interaction, self.state, battle_message)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_end(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Phase end cancelled.", view=None)


class InspectCoordinateModal(discord.ui.Modal, title="Inspect Coordinate"):
    coordinate = discord.ui.TextInput(label="Coordinate (e.g. 1A)", placeholder="1A", required=True, max_length=3)

    def __init__(self, state: BattleState):
        super().__init__()
        self.state = state

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.coordinate).strip().upper()
        if len(raw) < 2:
            await interaction.response.send_message("Invalid coordinate.", ephemeral=True)
            return
        row_str, col_letter = raw[:-1], raw[-1]
        if not row_str.isdigit() or col_letter not in GRID_COLUMNS:
            await interaction.response.send_message("Invalid coordinate format.", ephemeral=True)
            return
        row = int(row_str)
        if row < 1 or row > GRID_SIZE:
            await interaction.response.send_message("Coordinate is out of map bounds.", ephemeral=True)
            return
        coord = f"{row}{col_letter}"
        inspected_unit: Optional[Unit] = None
        for player in self.state.players.values():
            if player.coord == coord:
                inspected_unit = player
                break
        if inspected_unit is None:
            for enemy in self.state.enemies.values():
                if enemy.coord == coord:
                    inspected_unit = enemy
                    break

        move_coords: Optional[Set[str]] = None
        action_coords: Optional[Set[str]] = None
        supports_allies = False
        action_color = (220, 38, 38, 100)
        action_outline = (153, 27, 27, 255)
        if inspected_unit is not None:
            move_coords, action_coords, supports_allies = movement_and_action_ranges(self.state, inspected_unit)
            if supports_allies:
                action_color = (22, 163, 74, 115)
                action_outline = (21, 128, 61, 255)

        img = render_battle_map(
            self.state,
            highlight_move_coords=move_coords,
            highlight_action_coords=action_coords,
            action_color=action_color,
            action_outline=action_outline,
        )
        file = discord.File(img, filename="inspect_range.png")
        embed = build_inspect_range_embed(self.state, coord, inspected_unit, supports_allies=supports_allies)
        embed.set_image(url="attachment://inspect_range.png")
        await interaction.response.send_message(embed=embed, file=file, ephemeral=True)


class PlaceUnitSelect(discord.ui.Select):
    def __init__(self, prep_view: "PreparationView"):
        assigned_units = set(prep_view.deployed.values())
        available_units = [name for name in prep_view.state.players.keys() if name not in assigned_units]
        if prep_view.selected_unit and prep_view.selected_unit not in assigned_units:
            available_units.insert(0, prep_view.selected_unit)
        options = [discord.SelectOption(label=name, value=name) for name in available_units]
        super().__init__(placeholder="Choose a unit", options=options)
        self.prep_view = prep_view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.prep_view.selected_unit = self.values[0]
        await interaction.response.edit_message(
            content=f"Selected unit: **{self.prep_view.selected_unit}**. Choose a start slot and press Assign.",
            view=self.view,
        )


class PlaceSlotSelect(discord.ui.Select):
    def __init__(self, prep_view: "PreparationView"):
        taken_slots = set(prep_view.deployed.keys())
        available_slots = [slot for slot in prep_view.state.starting_positions if slot not in taken_slots]
        if prep_view.selected_slot and prep_view.selected_slot not in taken_slots:
            available_slots.insert(0, prep_view.selected_slot)
        options = [discord.SelectOption(label=slot, value=slot) for slot in available_slots]
        super().__init__(placeholder="Choose a start slot", options=options)
        self.prep_view = prep_view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.prep_view.selected_slot = self.values[0]
        await interaction.response.edit_message(
            content=f"Selected slot: **{self.prep_view.selected_slot}**. Press Assign to confirm.",
            view=self.view,
        )


class PlaceUnitsPickerView(discord.ui.View):
    def __init__(self, prep_view: "PreparationView"):
        super().__init__(timeout=300)
        self.prep_view = prep_view
        self.add_item(PlaceUnitSelect(prep_view))
        self.add_item(PlaceSlotSelect(prep_view))

    @discord.ui.button(label="Assign", style=discord.ButtonStyle.success)
    async def assign(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.prep_view.selected_unit or not self.prep_view.selected_slot:
            await interaction.response.send_message("Pick both a unit and a start slot first.", ephemeral=True)
            return

        unit = self.prep_view.selected_unit
        slot = self.prep_view.selected_slot
        if unit in self.prep_view.deployed.values():
            await interaction.response.send_message(f"**{unit}** is already assigned. Use Swap to change positions.", ephemeral=True)
            return
        if slot in self.prep_view.deployed:
            await interaction.response.send_message(f"**{slot}** is already taken. Choose an open slot.", ephemeral=True)
            return
        self.prep_view.deployed[slot] = unit
        self.prep_view.selected_unit = None
        self.prep_view.selected_slot = None

        await self.prep_view.refresh_preparation_message(interaction)
        await interaction.response.edit_message(content=f"Assigned **{unit}** to **{slot}**.", view=self)


class SwapUnitSelect(discord.ui.Select):
    def __init__(self, prep_view: "PreparationView", target: Literal["first", "second"]):
        assigned_units = sorted(prep_view.deployed.values())
        options = [discord.SelectOption(label=name, value=name) for name in assigned_units]
        placeholder = "Choose first assigned unit" if target == "first" else "Choose second assigned unit"
        super().__init__(placeholder=placeholder, options=options)
        self.prep_view = prep_view
        self.target = target

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_value = self.values[0]
        if self.target == "first":
            self.prep_view.swap_first_unit = selected_value
        else:
            self.prep_view.swap_second_unit = selected_value
        await interaction.response.edit_message(
            content=(
                "Select two assigned units, then press Swap Positions.\n"
                f"First: **{self.prep_view.swap_first_unit or '—'}** | "
                f"Second: **{self.prep_view.swap_second_unit or '—'}**"
            ),
            view=self.view,
        )


class SwapUnitsPickerView(discord.ui.View):
    def __init__(self, prep_view: "PreparationView"):
        super().__init__(timeout=300)
        self.prep_view = prep_view
        self.add_item(SwapUnitSelect(prep_view, "first"))
        self.add_item(SwapUnitSelect(prep_view, "second"))

    @discord.ui.button(label="Swap Positions", style=discord.ButtonStyle.success)
    async def swap_positions(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        first = self.prep_view.swap_first_unit
        second = self.prep_view.swap_second_unit
        if not first or not second:
            await interaction.response.send_message("Choose both units to swap.", ephemeral=True)
            return
        if first == second:
            await interaction.response.send_message("Choose two different units.", ephemeral=True)
            return

        first_slot = next((slot for slot, unit_name in self.prep_view.deployed.items() if unit_name == first), None)
        second_slot = next((slot for slot, unit_name in self.prep_view.deployed.items() if unit_name == second), None)
        if first_slot is None or second_slot is None:
            await interaction.response.send_message("Both units must already be assigned.", ephemeral=True)
            return

        self.prep_view.deployed[first_slot], self.prep_view.deployed[second_slot] = (
            self.prep_view.deployed[second_slot],
            self.prep_view.deployed[first_slot],
        )
        self.prep_view.swap_first_unit = None
        self.prep_view.swap_second_unit = None

        await self.prep_view.refresh_preparation_message(interaction)
        await interaction.response.edit_message(
            content=f"Swapped **{first}** and **{second}**.",
            view=self,
        )


class PreparationView(discord.ui.View):
    def __init__(self, state: BattleState):
        super().__init__(timeout=None)
        self.state = state
        self.message: Optional[discord.Message] = None
        self.deployed: Dict[str, str] = {}
        self.selected_unit: Optional[str] = None
        self.selected_slot: Optional[str] = None
        self.swap_first_unit: Optional[str] = None
        self.swap_second_unit: Optional[str] = None

    def auto_assign_all_units(self) -> Tuple[bool, str]:
        assigned_units = set(self.deployed.values())
        unassigned_units = [name for name in self.state.players.keys() if name not in assigned_units]
        available_slots = [slot for slot in self.state.starting_positions if slot not in self.deployed]

        if not unassigned_units:
            return False, "All player units are already assigned."
        if len(available_slots) < len(unassigned_units):
            return (
                False,
                (
                    "Not enough open start slots to auto assign all players "
                    f"({len(unassigned_units)} units, {len(available_slots)} slots)."
                ),
            )

        for slot, unit_name in zip(available_slots, unassigned_units):
            self.deployed[slot] = unit_name
        self.selected_unit = None
        self.selected_slot = None
        return True, f"Auto assigned {len(unassigned_units)} unit(s) to open start slots."

    async def refresh_preparation_message(self, interaction: discord.Interaction) -> None:
        visible_names = set(self.deployed.values())
        for deployed_slot, deployed_unit_name in self.deployed.items():
            self.state.players[deployed_unit_name].coord = deployed_slot

        prep_embed = build_preparation_embed(self.state, self.deployed)
        img = render_battle_map(
            self.state,
            show_player_spaces=True,
            visible_player_names=visible_names,
        )
        file = discord.File(img, filename="battle_map.png")
        prep_embed.set_image(url="attachment://battle_map.png")

        if self.message is None:
            return
        current_message: discord.Message = self.message
        if interaction.channel is not None:
            current_message = await interaction.channel.fetch_message(self.message.id)
            self.message = current_message

        await current_message.edit(
            embed=prep_embed,
            attachments=[file],
            view=self,
        )

    @discord.ui.button(label="Place Units", style=discord.ButtonStyle.primary)
    async def place_units(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        remaining_units = len(self.state.players) - len(self.deployed)
        remaining_slots = len(self.state.starting_positions) - len(self.deployed)
        if remaining_units <= 0 or remaining_slots <= 0:
            await interaction.response.send_message("All units and slots are already assigned.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Pick a unit and start slot, then assign.",
            view=PlaceUnitsPickerView(self),
            ephemeral=True,
        )

    @discord.ui.button(label="Auto", style=discord.ButtonStyle.primary)
    async def auto_assign(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        updated, message = self.auto_assign_all_units()
        if not updated:
            await interaction.response.send_message(message, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=False)
        await self.refresh_preparation_message(interaction)
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="Swap", style=discord.ButtonStyle.secondary)
    async def swap_units(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if len(self.deployed) < 2:
            await interaction.response.send_message("Assign at least two units before swapping.", ephemeral=True)
            return
        self.swap_first_unit = None
        self.swap_second_unit = None
        await interaction.response.send_message(
            "Select two assigned units, then press Swap Positions.",
            view=SwapUnitsPickerView(self),
            ephemeral=True,
        )

    @discord.ui.button(label="Inventory", style=discord.ButtonStyle.secondary)
    async def inventory(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "Convoy is currently empty, so there are no available item/weapon swaps yet.",
            ephemeral=True,
        )

    @discord.ui.button(label="Inspect", style=discord.ButtonStyle.secondary)
    async def inspect(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(InspectCoordinateModal(self.state))

    @discord.ui.button(label="Begin", style=discord.ButtonStyle.success)
    async def begin(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if len(self.deployed) < len(self.state.players):
            await interaction.response.send_message(
                f"Deploy all player units first ({len(self.deployed)}/{len(self.state.players)} assigned).",
                ephemeral=True,
            )
            return

        for slot, unit_name in self.deployed.items():
            self.state.players[unit_name].coord = slot

        await interaction.response.edit_message(view=None)
        await interaction.channel.send(
            embed=discord.Embed(title="Win Conditions", description="**Route the Enemy**", color=0x1D82B6)
        )
        await interaction.channel.send(
            embed=discord.Embed(title="Loss Conditions", description="**Lose All Units**", color=0xC0392B)
        )
        await interaction.channel.send(embed=phase_banner_embed("player"))
        await create_phase_battle_message(interaction, self.state)

class BattleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.battles: Dict[int, BattleState] = {}

    @app_commands.command(name="battle", description="Start a Fire Emblem style battle prototype.")
    async def battle(self, interaction: discord.Interaction) -> None:
        players = {u.name: clone_unit(u) for u in PLAYER_UNITS}
        enemies = {u.name: clone_unit(u) for u in ENEMY_UNITS}
        state = BattleState(players=players, enemies=enemies)
        channel_id = interaction.channel_id if interaction.channel_id is not None else 0
        self.battles[channel_id] = state

        await interaction.response.defer(thinking=True)

        img = render_battle_map(state, show_player_spaces=True, visible_player_names=set())
        file = discord.File(img, filename="battle_map.png")
        prep_view = PreparationView(state)
        embed = build_preparation_embed(state, prep_view.deployed)
        embed.set_image(url="attachment://battle_map.png")

        message = await interaction.followup.send(embed=embed, file=file, view=prep_view, wait=True)
        prep_view.message = message
        await message.edit(view=prep_view)

    @app_commands.command(name="battle2", description="Start Battle 2 on the custom terrain map.")
    async def battle2(self, interaction: discord.Interaction) -> None:
        players = {u.name: clone_unit(u) for u in PLAYER_UNITS}
        enemies = {u.name: clone_unit(u) for u in BATTLE2_ENEMY_UNITS}
        state = BattleState(
            players=players,
            enemies=enemies,
            terrain=dict(BATTLE2_TERRAIN),
            starting_positions=tuple(BATTLE2_STARTING_POSITIONS),
        )
        channel_id = interaction.channel_id if interaction.channel_id is not None else 0
        self.battles[channel_id] = state

        await interaction.response.defer(thinking=True)

        img = render_battle_map(state, show_player_spaces=True, visible_player_names=set())
        file = discord.File(img, filename="battle_map.png")
        prep_view = PreparationView(state)
        embed = build_preparation_embed(state, prep_view.deployed)
        embed.title = "Fire Emblem Mock Battle 2"
        embed.set_image(url="attachment://battle_map.png")

        message = await interaction.followup.send(embed=embed, file=file, view=prep_view, wait=True)
        prep_view.message = message
        await message.edit(view=prep_view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BattleCog(bot))
