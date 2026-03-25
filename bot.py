import asyncio
import os
import random
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

ASSET_DIR = "assets"
IMAGE_SCALE = 0.45


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


@dataclass
class Unit:
    name: str
    level: int
    klass: str
    stats: UnitStats
    coord: str
    image_name: Optional[str] = None


@dataclass
class BattleState:
    players: Dict[str, Unit]
    enemies: Dict[str, Unit]
    phase: Literal["player", "enemy"] = "player"
    moved_this_turn: Set[str] = field(default_factory=set)
    active_battle_message_id: Optional[int] = None


PLAYER_UNITS: List[Unit] = [
    Unit(
        name="Alear",
        level=1,
        klass="Dragon Child",
        stats=UnitStats(22, 6, 0, 5, 7, 5, 3, 5, 4, 4),
        coord="1A",
        image_name="alear.png",
    ),
    Unit(
        name="Vander",
        level=1,
        klass="Paladin",
        stats=UnitStats(40, 11, 5, 10, 8, 10, 8, 6, 8, 6),
        coord="1B",
        image_name="vander.png",
    ),
    Unit(
        name="Clanne",
        level=1,
        klass="Mage",
        stats=UnitStats(19, 1, 8, 11, 9, 4, 7, 4, 4, 4),
        coord="2A",
        image_name="clanne.png",
    ),
    Unit(
        name="Framme",
        level=1,
        klass="Martial Monk",
        stats=UnitStats(18, 3, 5, 8, 7, 4, 8, 5, 3, 4),
        coord="2B",
        image_name="framme.png",
    ),
]

ENEMY_UNITS: List[Unit] = [
    Unit("Enemy 1", 1, "Unknown", UnitStats(1, 1, 1, 1, 1, 1, 1, 1, 1, 4), "12L"),
    Unit("Enemy 2", 1, "Unknown", UnitStats(1, 1, 1, 1, 1, 1, 1, 1, 1, 4), "12K"),
    Unit("Enemy 3", 1, "Unknown", UnitStats(1, 1, 1, 1, 1, 1, 1, 1, 1, 4), "11L"),
    Unit("Enemy 4", 1, "Unknown", UnitStats(1, 1, 1, 1, 1, 1, 1, 1, 1, 4), "11K"),
]


class FireEmblemBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.battles: Dict[int, BattleState] = {}

    async def setup_hook(self) -> None:
        self.tree.add_command(battle)
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


def load_and_scale_sprite(filename: str) -> Optional[Image.Image]:
    path = os.path.join(ASSET_DIR, filename)
    if not os.path.exists(path):
        return None

    sprite = Image.open(path).convert("RGBA")
    target_w = max(1, int(sprite.width * IMAGE_SCALE))
    target_h = max(1, int(sprite.height * IMAGE_SCALE))
    return sprite.resize((target_w, target_h), Image.Resampling.LANCZOS)


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
        sprite = load_and_scale_sprite(player.image_name or "")
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


def state_summary(state: BattleState) -> str:
    moved = ", ".join(sorted(state.moved_this_turn)) if state.moved_this_turn else "None"
    phase_label = "Player Phase" if state.phase == "player" else "Enemy Phase"
    return "\n".join([
        f"## {phase_label}",
        f"### Player Units: **{len(state.players)}**",
        f"### Enemy Units: **{len(state.enemies)}**",
        f"\nMoved this phase: **{moved}**",
    ])


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


def find_enemy_move_destination(state: BattleState, enemy_name: str) -> str:
    enemy = state.enemies[enemy_name]
    current = enemy.coord
    blocked = occupied_coords(state, ignore_enemy=enemy_name)
    directions = ["up", "down", "left", "right"]
    for _ in range(enemy.stats.mov):
        random.shuffle(directions)
        next_coord = None
        for direction in directions:
            candidate = move_coord(current, direction)
            if candidate == current or candidate in blocked:
                continue
            next_coord = candidate
            break
        if next_coord is None:
            break
        current = next_coord
        blocked.add(current)
    return current


async def run_enemy_phase(interaction: discord.Interaction, state: BattleState, battle_message: discord.Message) -> None:
    state.phase = "enemy"
    state.moved_this_turn.clear()
    await lock_battle_message(interaction, battle_message.id)
    active_enemy_message = await create_phase_battle_message(interaction, state)

    for enemy_name in state.enemies:
        destination = find_enemy_move_destination(state, enemy_name)
        state.enemies[enemy_name].coord = destination
        state.moved_this_turn.add(enemy_name)
        await refresh_battle_message(interaction, state, active_enemy_message)
        await asyncio.sleep(0.4)

    state.phase = "player"
    state.moved_this_turn.clear()
    await lock_battle_message(interaction, active_enemy_message.id)
    await create_phase_battle_message(interaction, state)


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
        self.state.moved_this_turn.add(self.unit_name)

        await interaction.response.edit_message(content=f"Confirmed {self.unit_name} to `{self.preview_coord}`.", view=None)
        active_message_id = self.state.active_battle_message_id
        if active_message_id is None:
            await interaction.followup.send("Unable to refresh battle map message.", ephemeral=True)
            return
        battle_message = await interaction.channel.fetch_message(active_message_id)
        await refresh_battle_message(interaction, self.state, battle_message)
        if self.state.phase == "player" and len(self.state.moved_this_turn) == len(self.state.players):
            await interaction.followup.send("All player units acted. Ending Player Phase.", ephemeral=True)
            await run_enemy_phase(interaction, self.state, battle_message)


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

    players = {u.name: Unit(**u.__dict__) for u in PLAYER_UNITS}
    enemies = {u.name: Unit(**u.__dict__) for u in ENEMY_UNITS}
    state = BattleState(players=players, enemies=enemies)
    client.battles[interaction.channel_id] = state

    img = render_battle_map(state)
    file = discord.File(img, filename="battle_map.png")
    embed = discord.Embed(title="Fire Emblem Mock Battle", description=state_summary(state), color=0x5C9E31)
    embed.set_image(url="attachment://battle_map.png")

    await interaction.response.send_message(embed=embed, file=file, view=BattleView(client, state, 0))
    message = await interaction.original_response()
    state.active_battle_message_id = message.id
    await message.edit(view=BattleView(client, state, message.id))


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
