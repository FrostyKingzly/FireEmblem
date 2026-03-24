# Fire Emblem Discord Battle Prototype

This repo now includes a `/battle` slash command prototype for Discord that:

- Starts a battle immediately.
- Uses a 12x12 board with coordinates (`1A` to `12L`), where letters are horizontal and numbers are vertical.
- Spawns 4 playable units with the provided level/class/base stats:
  - Alear (`1A`)
  - Vander (`1B`)
  - Clanne (`2A`)
  - Framme (`2B`)
- Spawns 4 level-1 placeholder enemies in the opposite corner (`12L`, `12K`, `11L`, `11K`).
- Shows a **Move** button under the battle embed.
- Lets you pick a unit, then move it with **Left/Up/Right/Down** and **Confirm**.
- Updates the original map embed after confirm.
- Prevents moving the same ally twice in one turn (phase logic can be added later).

## Setup

1. Install dependencies:

```bash
python -m pip install -r requirements.txt
```

2. Add player sprite images (the 4 images you provided) to `assets/` with these exact names:

- `assets/alear.png`
- `assets/vander.png`
- `assets/clanne.png`
- `assets/framme.png`

The renderer scales each sprite to ~45% of its original dimensions to fit cells.

3. Set your bot token:

```bash
export DISCORD_BOT_TOKEN="your-token-here"
```

4. Run:

```bash
python bot.py
```

## Notes

- Grid boundaries are enforced (units cannot move outside `1A`..`12L`).
- If any sprite is missing, a fallback colored token is drawn in that tile.
