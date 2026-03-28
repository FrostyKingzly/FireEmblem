import asyncio
import os
import random
from collections import deque
from dataclasses import dataclass, field
from io import BytesIO
from typing import Dict, List, Literal, Optional, Set, Tuple

import discord
from discord import app_commands
from PIL import Image, ImageDraw

# 12x12 map with A-L columns and 1-12 rows.
GRID_COLUMNS = [chr(ord("A") + i) for i in range(12)]
GRID_SIZE = 12
CELL_SIZE = 96
GRID_LINE_WIDTH = 4
BOARD_PADDING = 4
BOARD_BG = (216, 216, 216, 255)
GRID_COLOR = (0, 0, 0, 255)
BACKGROUND_KEY_DISTANCE = 45

ASSET_DIR = "assets"
TEST_MAP_IMAGE_PATH = os.path.join(ASSET_DIR, "battle2_map.png")


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


@dataclass
class BattleState:
    players: Dict[str, Unit]
    enemies: Dict[str, Unit]
    phase: Literal["player", "enemy"] = "player"
    moved_this_turn: Set[str] = field(default_factory=set)
    active_battle_message_id: Optional[int] = None
    provoked_enemies: Set[str] = field(default_factory=set)
    battle_over: bool = False


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
    "Heal": Weapon(name="Heal", might=0, hit=100, crit=0, weight=0, rng_min=1, rng_max=1, kind="staff", heal_power=10),
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

ENEMY_UNITS: List[Unit] = [
    Unit(
        "Enemy 1",
        1,
        "Unknown",
        UnitStats(10, 1, 1, 1, 1, 1, 1, 1, 1, 4),
        "12L",
        behavior="aggressive",
        inventory=[WEAPONS["Iron Sword"]],
    ),
    Unit(
        "Enemy 2",
        1,
        "Unknown",
        UnitStats(10, 1, 1, 1, 1, 1, 1, 1, 1, 4),
        "12K",
        behavior="aggressive",
        inventory=[WEAPONS["Iron Sword"]],
    ),
    Unit(
        "Enemy 3",
        1,
        "Unknown",
        UnitStats(10, 1, 1, 1, 1, 1, 1, 1, 1, 4),
        "11L",
        behavior="aggressive",
        inventory=[WEAPONS["Iron Sword"]],
    ),
    Unit(
        "Enemy 4",
        1,
        "Unknown",
        UnitStats(10, 1, 1, 1, 1, 1, 1, 1, 1, 4),
        "11K",
        behavior="aggressive",
        inventory=[WEAPONS["Iron Sword"]],
    ),
]


class FireEmblemBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.battles: Dict[int, BattleState] = {}

    async def setup_hook(self) -> None:
        self.tree.add_command(battle)
        self.tree.add_command(battle2)
        await self.tree.sync()


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


def hp_bar(after_hp: int, max_hp: int, *, width: int = 10) -> str:
    fill = round((after_hp / max_hp) * width) if max_hp else 0
    fill = clamp(fill, 0, width)
    return "🟦" * fill + "⬜" * (width - fill)


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


def create_base_grid() -> Image.Image:
    side = GRID_SIZE * CELL_SIZE + GRID_LINE_WIDTH
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


def load_sprite_from_assets(unit_name: str) -> Optional[Image.Image]:
    filename = f"{unit_name}.png"
    path = os.path.join(ASSET_DIR, filename)
    if not os.path.exists(path):
        return None
    return Image.open(path).convert("RGBA")


def draw_fallback_unit(draw: ImageDraw.ImageDraw, coord: str, color: Tuple[int, int, int, int]) -> None:
    x, y = cell_origin(coord)
    cx = x + CELL_SIZE // 2
    cy = y + CELL_SIZE // 2
    radius = CELL_SIZE // 3
    draw.ellipse([(cx - radius, cy - radius), (cx + radius, cy + radius)], fill=color, outline=(0, 0, 0, 255), width=3)


def render_battle_map(state: BattleState) -> BytesIO:
    board = create_base_grid()
    draw = ImageDraw.Draw(board)

    for enemy in state.enemies.values():
        draw_fallback_unit(draw, enemy.coord, (220, 50, 50, 255))

    for player in state.players.values():
        sprite = load_sprite_from_assets(player.name)
        if sprite is None:
            draw_fallback_unit(draw, player.coord, (50, 120, 220, 255))
            continue
        x, y = cell_origin(player.coord)
        px = x + (CELL_SIZE - sprite.width) // 2
        py = y + (CELL_SIZE - sprite.height) // 2
        board.alpha_composite(sprite, (px, py))

    buf = BytesIO()
    board.save(buf, format="PNG")
    buf.seek(0)
    return buf


def render_test_map() -> BytesIO:
    board = create_base_grid()
    if os.path.exists(TEST_MAP_IMAGE_PATH):
        test_map = Image.open(TEST_MAP_IMAGE_PATH).convert("RGBA")
        if test_map.size != board.size:
            test_map = test_map.resize(board.size, Image.NEAREST)
        board.alpha_composite(test_map, (0, 0))

    buf = BytesIO()
    board.save(buf, format="PNG")
    buf.seek(0)
    return buf


def state_summary(state: BattleState) -> str:
    moved = ", ".join(sorted(state.moved_this_turn)) if state.moved_this_turn else "None"
    phase_label = "Player Phase" if state.phase == "player" else "Enemy Phase"
    poison_lines = []
    for team in (state.players, state.enemies):
        for unit in team.values():
            if unit.poison_stacks > 0:
                poison_lines.append(f"{unit.name}({unit.poison_stacks})")
    poison_text = ", ".join(poison_lines) if poison_lines else "None"
    return "\n".join([
        f"## {phase_label}",
        f"### Player Units: **{len(state.players)}**",
        f"### Enemy Units: **{len(state.enemies)}**",
        f"\nActed this phase: **{moved}**",
        f"Poison stacks: **{poison_text}**",
    ])


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
    await message.edit(embed=embed, attachments=[file], view=BattleView(interaction.client, state, message.id))


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
    await message.edit(view=BattleView(interaction.client, state, message.id))
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


def resolve_combat_round(attacker: Unit, defender: Unit, lines: List[str]) -> bool:
    hit = calc_hit(attacker, defender)
    crit = calc_crit(attacker, defender)
    if random.randint(1, 100) > hit:
        lines.append(f"{attacker.name} attacks with {attacker.equipped_weapon.name}, but misses.")
        return defender.current_hp <= 0

    dmg = calc_damage(attacker, defender)
    critted = random.randint(1, 100) <= crit
    total = dmg * 3 if critted else dmg
    defender.current_hp = max(0, defender.current_hp - total)
    crit_text = " **CRITICAL!**" if critted else ""
    lines.append(
        f"{attacker.name} hits {defender.name} for **{total}** damage.{crit_text} "
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
        defender_down = resolve_combat_round(enemy, target, lines)
        if defender_down:
            lines.append(f"💀 {target.name} is defeated!")
            state.players.pop(target.name, None)
            state.moved_this_turn.discard(target.name)
        elif in_weapon_range(target, enemy):
            attacker_down = resolve_combat_round(target, enemy, lines)
            apply_after_combat_skill(target, enemy, lines=lines)
            if attacker_down:
                lines.append(f"💀 {enemy.name} is defeated!")
                state.enemies.pop(enemy.name, None)

        public_embed = discord.Embed(title="Battle Log", description="\n".join(lines), color=0xD67F2C)
        await interaction.channel.send(embed=public_embed)
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
            f"HP: **{enemy_after_hp}/{enemy.stats.hp}** {hp_bar(enemy_after_hp, enemy.stats.hp)}",
            "",
            f"Dmg: **{enemy_dmg}**",
            f"Hit: **{enemy_hit}%**",
            f"Crit: **{enemy_crit}%**",
        ]),
        inline=True,
    )
    embed.set_footer(text="Fight to commit action, Weapon to swap, Back to undo movement.")
    return embed


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
        healer = self.state.players[self.healer_name]
        ally_name = self.values[0]
        ally = self.state.players[ally_name]
        amount = heal_amount(healer)
        before = ally.current_hp
        ally.current_hp = min(ally.stats.hp, ally.current_hp + amount)
        self.state.moved_this_turn.add(self.healer_name)
        await interaction.response.edit_message(
            content=f"{healer.name} uses {healer.equipped_weapon.name} on {ally.name}: {before} → {ally.current_hp} HP.",
            embed=None,
            view=None,
        )
        active_message_id = self.state.active_battle_message_id
        if active_message_id is not None:
            battle_message = await interaction.channel.fetch_message(active_message_id)
            await refresh_battle_message(interaction, self.state, battle_message)


class HealTargetView(discord.ui.View):
    def __init__(self, state: BattleState, healer_name: str):
        super().__init__(timeout=180)
        self.add_item(HealTargetSelect(state, healer_name))


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

        defender_down = resolve_combat_round(player, enemy, lines)
        if defender_down:
            lines.append(f"💀 {enemy.name} is defeated!")
            self.state.enemies.pop(enemy.name, None)
        elif in_weapon_range(enemy, player):
            attacker_down = resolve_combat_round(enemy, player, lines)
            if attacker_down:
                lines.append(f"💀 {player.name} is defeated!")
                self.state.players.pop(player.name, None)
        else:
            lines.append(f"{enemy.name} cannot counterattack (out of range).")

        apply_after_combat_skill(player, enemy, lines=lines)
        self.state.moved_this_turn.add(self.player_name)
        await interaction.response.edit_message(content="Combat resolved.", embed=None, view=None)

        public_embed = discord.Embed(title="Battle Log", description="\n".join(lines), color=0xD67F2C)
        await interaction.channel.send(embed=public_embed)

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
    def __init__(self, client: FireEmblemBot, state: BattleState, unit_name: str, battle_message_id: int):
        super().__init__(timeout=300)
        self.client = client
        self.state = state
        self.unit_name = unit_name
        self.battle_message_id = battle_message_id
        self.start_coord = state.players[unit_name].coord
        self.preview_coord = self.start_coord
        self.steps_taken = 0

    @property
    def movement_cap(self) -> int:
        return self.state.players[self.unit_name].stats.mov

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

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

        self.preview_coord = candidate
        self.steps_taken += 1
        await interaction.response.edit_message(
            content=(
                f"{self.unit_name} preview position: `{self.preview_coord}` "
                f"({self.steps_taken}/{self.movement_cap} mov)"
            ),
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

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        blocked = occupied_coords(self.state, ignore_player=self.unit_name)
        if self.preview_coord in blocked:
            await interaction.response.send_message(
                "You can't end movement on an occupied tile.",
                ephemeral=True,
            )
            return

        self.state.players[self.unit_name].coord = self.preview_coord
        await interaction.response.edit_message(content=f"Confirmed {self.unit_name} to `{self.preview_coord}`.", view=None)
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
                await interaction.followup.send(
                    "Choose an adjacent ally to heal.",
                    view=HealTargetView(self.state, self.unit_name),
                    ephemeral=True,
                )
                return
            await interaction.followup.send("No injured ally in staff range. You can still End phase.", ephemeral=True)
            return

        in_range_enemies = [enemy for enemy in self.state.enemies.values() if in_weapon_range(player, enemy)]
        if in_range_enemies:
            enemy = in_range_enemies[0]
            embed = build_prebattle_embed(player, enemy)
            await interaction.followup.send(
                "Enemy in range. Attack?",
                embed=embed,
                view=PreBattleView(self.state, self.unit_name, enemy.name, self.start_coord),
                ephemeral=True,
            )
            return
        await interaction.followup.send("No enemies in range. You can move this unit again or choose End phase.", ephemeral=True)


class PickUnitView(discord.ui.View):
    def __init__(self, client: FireEmblemBot, state: BattleState, battle_message_id: int):
        super().__init__(timeout=300)
        self.client = client
        self.state = state
        self.battle_message_id = battle_message_id

        available = [u.name for u in state.players.values() if u.name not in state.moved_this_turn]
        options = [discord.SelectOption(label=name, value=name) for name in available]
        self.add_item(UnitSelect(options, client, state, battle_message_id))


class UnitSelect(discord.ui.Select):
    def __init__(self, options: List[discord.SelectOption], client: FireEmblemBot, state: BattleState, battle_message_id: int):
        super().__init__(placeholder="Choose a unit to move", options=options)
        self.client = client
        self.state = state
        self.battle_message_id = battle_message_id

    async def callback(self, interaction: discord.Interaction) -> None:
        unit_name = self.values[0]
        view = DirectionView(self.client, self.state, unit_name, self.battle_message_id)
        await interaction.response.edit_message(content=f"Moving {unit_name}. Use direction buttons then Confirm.", view=view)


class BattleView(discord.ui.View):
    def __init__(self, client: FireEmblemBot, state: BattleState, battle_message_id: int):
        super().__init__(timeout=None)
        self.client = client
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

        picker = PickUnitView(self.client, self.state, self.battle_message_id)
        await interaction.response.send_message("Pick a unit to move:", view=picker, ephemeral=True)

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


@app_commands.command(name="battle", description="Start a Fire Emblem style battle prototype.")
async def battle(interaction: discord.Interaction) -> None:
    assert interaction.client is not None
    client = interaction.client
    if not isinstance(client, FireEmblemBot):
        await interaction.response.send_message("Bot client misconfigured.", ephemeral=True)
        return

    players = {u.name: clone_unit(u) for u in PLAYER_UNITS}
    enemies = {u.name: clone_unit(u) for u in ENEMY_UNITS}
    state = BattleState(players=players, enemies=enemies)
    client.battles[interaction.channel_id] = state

    img = render_battle_map(state)
    file = discord.File(img, filename="battle_map.png")
    intro = "\n".join([
        state_summary(state),
        "",
        "### Win Condition: **Route the Enemy**",
        "### Loss Condition: **Lose All Units**",
    ])
    embed = discord.Embed(title="Fire Emblem Mock Battle", description=intro, color=0x5C9E31)
    embed.set_image(url="attachment://battle_map.png")

    await interaction.response.send_message(embed=embed, file=file, view=BattleView(client, state, 0))
    message = await interaction.original_response()
    state.active_battle_message_id = message.id
    await message.edit(view=BattleView(client, state, message.id))
    await interaction.channel.send(embed=phase_banner_embed("player"))


@app_commands.command(name="battle2", description="Show a no-units test map.")
async def battle2(interaction: discord.Interaction) -> None:
    img = render_test_map()
    file = discord.File(img, filename="battle2_map.png")
    description_lines = [
        "## Test Map",
        "### Player Units: **0**",
        "### Enemy Units: **0**",
        "",
        "No enemies or players are spawned on this map.",
    ]
    if os.path.exists(TEST_MAP_IMAGE_PATH):
        description_lines.append("Loaded terrain from `assets/battle2_map.png`.")
    else:
        description_lines.append("`assets/battle2_map.png` not found, showing fallback grid.")
    embed = discord.Embed(title="Fire Emblem Test Map", description="\n".join(description_lines), color=0x5C9E31)
    embed.set_image(url="attachment://battle2_map.png")
    await interaction.response.send_message(embed=embed, file=file)


def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token and os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == "DISCORD_BOT_TOKEN":
                    token = value.strip().strip("\"'")
                    break

    if not token:
        raise RuntimeError(
            "Set DISCORD_BOT_TOKEN in your environment, or add DISCORD_BOT_TOKEN=... to a .env file."
        )

    bot = FireEmblemBot()
    bot.run(token)


if __name__ == "__main__":
    main()
