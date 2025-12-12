# pacman_tile_based_tunnel_fixed_fluid.py
"""
Tile-based Pac-Man clone with fluid tunnel wrap fix.
- Pac-Man moves tile-center -> tile-center (prevents touching walls)
- Tunnel warp (far-right <-> far-left middle) is fluid: Pac-Man keeps moving automatically
- Pellets + power pellets picked up reliably
- 4 ghosts with frightened/eyes behavior
- Fullscreen-adaptive display
"""
import pygame, sys, random, math
from collections import deque

pygame.init()
try:
    pygame.mixer.init()
except Exception:
    pass

# ========== CONFIG ==========
TILE = 24                # base tile size (pixels) -- will be adapted if screen too small
MAZE = [
"############################",
"#............##............#",
"#.####.#####.##.#####.####.#",
"#o####.#####.##.#####.####o#",
"#.####.#####.##.#####.####.#",
"#..........................#",
"#.####.##.########.##.####.#",
"#.####.##.########.##.####.#",
"#......##....##....##......#",
"######.#####.##.#####.######",
"     #.#####.##.#####.#     ",
"     #.##          ##.#     ",
"     #.## ###--### ##.#     ",
"######.## #      # ##.######",
"      .   # GG   #   .      ",
"######.## #      # ##.######",
"     #.## ######## ##.#     ",
"     #.##          ##.#     ",
"     #.## ######## ##.#     ",
"######.## ######## ##.######",
"#............##............#",
"#.####.#####.##.#####.####.#",
"#.####.#####.##.#####.####.#",
"#o..##................##..o#",
"###.##.##.########.##.##.###",
"###.##.##.########.##.##.###",
"#......##....##....##......#",
"#.##########.##.##########.#",
"#.##########.##.##########.#",
"#..........................#",
"############################",
]
MAZE_W = len(MAZE[0])
MAZE_H = len(MAZE)

# Colors
BLACK  = (0,0,0)
NAVY   = (0,0,128)
WHITE  = (255,255,255)
YELLOW = (255,220,0)
RED    = (255,0,0)        # Blinky
PINK   = (255,184,255)    # Pinky
CYAN   = (0,255,255)      # Inky
ORANGE = (255,165,0)      # Clyde
BLUE   = (0,0,255)        # frightened
GHOST_COLORS = [RED, PINK, CYAN, ORANGE]

FPS = 60
clock = pygame.time.Clock()

# Fullscreen and adapt TILE if needed
screen = pygame.display.set_mode((0,0), pygame.FULLSCREEN)
SCREEN_W, SCREEN_H = screen.get_size()
if MAZE_W * TILE > SCREEN_W or MAZE_H * TILE > SCREEN_H:
    TILE = max(8, min(SCREEN_W // MAZE_W, SCREEN_H // MAZE_H))
MAZE_PIXEL_W = MAZE_W * TILE
MAZE_PIXEL_H = MAZE_H * TILE
OFFSET_X = (SCREEN_W - MAZE_PIXEL_W) // 2
OFFSET_Y = (SCREEN_H - MAZE_PIXEL_H) // 2

FONT = pygame.font.SysFont("Arial", 20)

# ========== UTILS ==========
def in_bounds(x,y):
    return 0 <= x < MAZE_W and 0 <= y < MAZE_H

def is_wall(x,y):
    if not in_bounds(x,y): return True
    return MAZE[y][x] == '#'

def is_open(x,y):
    if not in_bounds(x,y): return False
    return MAZE[y][x] != '#'

def tile_center_pixel(tx, ty):
    return OFFSET_X + tx*TILE + TILE//2, OFFSET_Y + ty*TILE + TILE//2

def draw_text(surf, txt, x, y, color=WHITE):
    surf.blit(FONT.render(txt, True, color), (x,y))

# ========== LEVEL SETUP ==========
pellets = set()
power_pellets = set()
ghost_starts = []
pac_start = None

for y,row in enumerate(MAZE):
    for x,ch in enumerate(row):
        if ch == '.': pellets.add((x,y))
        elif ch == 'o': power_pellets.add((x,y))
        elif ch == 'G': ghost_starts.append((x,y))
        elif ch == 'P': pac_start = (x,y)

if pac_start is None:
    pac_start = (14,23)
if len(ghost_starts) < 4:
    ghost_starts = [(13,14),(14,14),(12,14),(15,14)]

# also add pellets on ' ' open tiles (outside ghost box)
for y,row in enumerate(MAZE):
    for x,ch in enumerate(row):
        if ch == ' ':
            if not (10 <= x <= 17 and 11 <= y <= 16):
                pellets.add((x,y))

# ========== GAME MODE ==========
MODE_SEQUENCE = [("scatter",7),("chase",20),("scatter",7),("chase",20),
                 ("scatter",5),("chase",20),("scatter",5),("chase",9999)]
mode_index = 0
mode_timer = MODE_SEQUENCE[mode_index][1] * FPS
FRIGHT_DURATION = 8 * FPS

# ========== ENTITIES (TILE-BASED Movement with warp support) ==========
class TileMover:
    """Base for tile-based moving entity with warp support."""
    def __init__(self, tile):
        self.tx, self.ty = tile
        cx, cy = tile_center_pixel(self.tx, self.ty)
        self.px = float(cx)
        self.py = float(cy)
        self.dx, self.dy = 0,0            # direction in tile deltas (-1,0,1)
        self.next_dx, self.next_dy = 0,0 # queued dir
        self.moving = False               # whether moving between centers
        self.speed = 2.2                  # pixels/frame while moving
        # warp attributes: when True, arrival will snap to warp_target
        self.warping = False
        self.warp_target = None

    def at_center(self):
        cx, cy = tile_center_pixel(self.tx, self.ty)
        return abs(self.px - cx) < 1.0 and abs(self.py - cy) < 1.0

    def start_move(self, dx, dy):
        """Attempt to start a move into neighbor tile. Supports wrap if needed."""
        nx, ny = self.tx + dx, self.ty + dy

        # Normal open tile -> start move
        if is_open(nx, ny):
            self.dx, self.dy = dx, dy
            self.moving = True
            self.warping = False
            self.warp_target = None
            return True

        # Special-case: allow wrap-moving horizontally through tunnel
        # If target x is out-of-bounds or is a wall but corresponding wrapped tile is open,
        # permit the move and set warp_target to the wrapped tile.
        # We only allow horizontal wraps (original Pac-Man tunnels).
        if dy == 0 and (nx < 0 or nx >= MAZE_W or not is_open(nx, ny)):
            wrapped_x = nx % MAZE_W
            if is_open(wrapped_x, ny):
                # Start move toward the logical tile (tx+dx, ty+dy). We'll compute pixel target as usual,
                # but remember warp_target so when arrival occurs we snap to wrapped tile center.
                self.dx, self.dy = dx, dy
                self.moving = True
                self.warping = True
                self.warp_target = (wrapped_x, ny)
                return True

        # Otherwise can't start
        return False

    def stop(self):
        self.dx = self.dy = 0
        self.moving = False
        self.warping = False
        self.warp_target = None
        cx, cy = tile_center_pixel(self.tx, self.ty)
        self.px, self.py = float(cx), float(cy)

    def update_position_pixels(self):
        """Move pixels toward center of destination tile; handle warp snap on arrival.
           IMPORTANT: on warp arrival we now attempt to continue moving automatically
           in the same direction so movement is fluid through tunnels.
        """
        if not self.moving: return
        target_tx = self.tx + self.dx
        target_ty = self.ty + self.dy
        target_cx, target_cy = tile_center_pixel(target_tx, target_ty)
        vx = target_cx - self.px
        vy = target_cy - self.py
        dist = math.hypot(vx, vy)
        if dist <= self.speed:
            # Arrive: if warping, snap to warp target and then attempt to continue automatically
            if self.warping and self.warp_target is not None:
                # snap to wrapped tile center
                self.tx, self.ty = self.warp_target
                cx, cy = tile_center_pixel(self.tx, self.ty)
                self.px, self.py = float(cx), float(cy)
                # clear warp flags
                self.warping = False
                self.warp_target = None
                self.moving = False
                # Attempt to continue moving in same direction automatically:
                # if the next tile in same direction (from new position) is open, start_move again.
                # This makes tunnel movement fluid and keeps direction.
                self.start_move(self.dx, self.dy)
            else:
                # normal arrival
                self.tx += self.dx
                self.ty += self.dy
                self.px, self.py = float(target_cx), float(target_cy)
                self.moving = False
        else:
            self.px += (vx / dist) * self.speed
            self.py += (vy / dist) * self.speed

class Pacman(TileMover):
    def __init__(self, tile):
        super().__init__(tile)
        self.lives = 3
        self.score = 0
        self.alive = True
        self.respawn_timer = 0
        self.radius = TILE//2 - 2
        self.speed = 2.6

    def queue_dir(self, dx, dy):
        self.next_dx, self.next_dy = dx,dy

    def update(self):
        # If not moving, try queued dir first then current dir
        if not self.moving:
            if (self.next_dx, self.next_dy) != (0,0):
                if self.start_move(self.next_dx, self.next_dy):
                    # accepted queued turn: clear queue
                    self.next_dx, self.next_dy = 0,0
                else:
                    # try current direction
                    if (self.dx, self.dy) != (0,0):
                        self.start_move(self.dx, self.dy)
            else:
                if (self.dx, self.dy) != (0,0):
                    self.start_move(self.dx, self.dy)

        # Move pixels
        self.update_position_pixels()

        # Note: previously we snapped to other side and forced dx/dy=0 here.
        # That forced the player to press a key after warp.
        # We removed that behavior so movement continues fluidly (handled in update_position_pixels).

    def pick_up(self):
        # Only when centered pick up pellets/power
        if not self.at_center(): return
        if (self.tx, self.ty) in pellets:
            pellets.remove((self.tx, self.ty))
            self.score += 10
        if (self.tx, self.ty) in power_pellets:
            power_pellets.remove((self.tx, self.ty))
            self.score += 50
            trigger_fright()

    def die(self):
        self.lives -= 1
        self.alive = False
        self.respawn_timer = 2 * FPS

    def respawn(self):
        self.tx, self.ty = pac_start
        cx,cy = tile_center_pixel(self.tx, self.ty)
        self.px, self.py = float(cx), float(cy)
        self.dx = self.dy = 0
        self.moving = False
        self.warping = False
        self.warp_target = None
        self.alive = True

    def draw(self, surf):
        x,y = int(self.px), int(self.py)
        pygame.draw.circle(surf, YELLOW, (x,y), self.radius)
        ddx,ddy = self.dx, self.dy
        if not self.moving and (self.next_dx,self.next_dy) != (0,0):
            ddx,ddy = self.next_dx, self.next_dy
        if (ddx,ddy) != (0,0):
            dx,dy = ddx,ddy
            wedge = [
                (x,y),
                (x + int(dx * self.radius * 1.05) - int(dy * self.radius * 0.2),
                 y + int(dy * self.radius * 1.05) + int(dx * self.radius * 0.2)),
                (x + int(dx * self.radius * 1.05) + int(dy * self.radius * 0.2),
                 y + int(dy * self.radius * 1.05) - int(dx * self.radius * 0.2))
            ]
            pygame.draw.polygon(surf, BLACK, wedge)

class Ghost(TileMover):
    def __init__(self, tile, idx):
        super().__init__(tile)
        self.idx = idx
        self.mode = "scatter"    # scatter/chase/frightened/eyes
        self.dead = False        # when true -> eyes mode
        self.fright_timer = 0
        self.speed_chase = 1.6
        self.speed_scatter = 1.4
        self.speed_fright = 1.0
        self.speed_eyes = 3.2
        self.start_tile = tile

    def set_frightened(self):
        if not self.dead:
            self.mode = "frightened"
            self.fright_timer = FRIGHT_DURATION

    def kill_and_become_eyes(self):
        self.dead = True
        self.mode = "eyes"
        self.dx = self.dy = 0
        self.moving = False
        self.warping = False
        self.warp_target = None

    def target_tile(self, pac, blinky_tile):
        px,py = pac.tx, pac.ty
        if self.idx == 0:
            return (px,py)
        elif self.idx == 1:
            tx = px + 4 * pac.dx
            ty = py + 4 * pac.dy
            return (tx,ty)
        elif self.idx == 2:
            if blinky_tile is None:
                bx,by = px,py
            else:
                bx,by = blinky_tile
            tx = px + 2 * pac.dx
            ty = py + 2 * pac.dy
            return (tx + (tx - bx), ty + (ty - by))
        else:
            dist = math.hypot(px - self.tx, py - self.ty)
            if dist > 8:
                return (px,py)
            return (0, MAZE_H - 1)

    def choose_direction_toward(self, target):
        gx,gy = self.tx, self.ty
        choices = []
        for d in [(1,0),(-1,0),(0,1),(0,-1)]:
            nx,ny = gx + d[0], gy + d[1]
            if is_open(nx,ny):
                if (-d[0],-d[1]) == (self.dx, self.dy):
                    continue
                choices.append(d)
        if not choices:
            choices = [(-self.dx, -self.dy)]
        best=None; bestd=1e9
        for d in choices:
            nx,ny = gx + d[0], gy + d[1]
            dx = nx - target[0]; dy = ny - target[1]
            dist = dx*dx + dy*dy
            if dist < bestd:
                bestd = dist; best = d
        if best:
            self.dx, self.dy = best

    def update(self, pac, blinky_tile):
        if self.mode == "frightened" and not self.dead:
            self.fright_timer -= 1
            if self.fright_timer <= 0:
                self.mode = "chase"

        if self.mode == "eyes":
            if not self.moving:
                self.choose_direction_toward((14,14))
                self.start_move(self.dx, self.dy)
            self.speed = self.speed_eyes
            self.update_position_pixels()
            if (self.tx, self.ty) == (14,14) and not self.moving:
                self.dead = False
                self.mode = "scatter"
                self.dx, self.dy = 0,0
            return

        if not self.moving:
            gx,gy = self.tx, self.ty
            candid = []
            for d in [(1,0),(-1,0),(0,1),(0,-1)]:
                nx,ny = gx + d[0], gy + d[1]
                if is_open(nx,ny):
                    if (-d[0], -d[1]) == (self.dx, self.dy):
                        continue
                    candid.append(d)
            if not candid:
                candid = [(-self.dx, -self.dy)]

            if self.mode == "frightened":
                self.dx, self.dy = random.choice(candid)
            elif self.mode == "scatter":
                corners = [(MAZE_W-1,0),(0,0),(MAZE_W-1,MAZE_H-1),(0,MAZE_H-1)]
                target = corners[self.idx]
                self.choose_direction_toward(target)
            else:
                target = self.target_tile(pac, blinky_tile)
                self.choose_direction_toward(target)

            self.start_move(self.dx, self.dy)

        if self.mode == "frightened":
            self.speed = self.speed_fright
        else:
            self.speed = self.speed_chase if self.mode == "chase" else self.speed_scatter
        self.update_position_pixels()

    def draw(self, surf):
        x,y = int(self.px), int(self.py)
        if self.mode == "frightened" and not self.dead:
            color = BLUE
        else:
            color = GHOST_COLORS[self.idx]
        pygame.draw.circle(surf, color, (x, y - 4), TILE//2 - 2)
        pygame.draw.circle(surf, WHITE, (x - 6, y - 6), 4)
        pygame.draw.circle(surf, WHITE, (x + 6, y - 6), 4)
        pdx = int(self.dx * 2); pdy = int(self.dy * 2)
        pygame.draw.circle(surf, (0,0,0), (x - 6 + pdx, y - 6 + pdy), 2)
        pygame.draw.circle(surf, (0,0,0), (x + 6 + pdx, y - 6 + pdy), 2)

# ========== GAME OBJECTS ==========
player = Pacman(pac_start)
ghosts = [Ghost(ghost_starts[i], i) for i in range(4)]

def trigger_fright():
    global ghosts_eaten_in_power
    ghosts_eaten_in_power = 0
    for g in ghosts:
        if not g.dead:
            g.mode = "frightened"
            g.fright_timer = FRIGHT_DURATION

def set_global_mode(mode):
    for g in ghosts:
        if g.mode not in ("frightened", "eyes"):
            g.mode = mode

def draw_maze():
    screen.fill(BLACK)
    for y,row in enumerate(MAZE):
        for x,ch in enumerate(row):
            px = OFFSET_X + x*TILE
            py = OFFSET_Y + y*TILE
            if ch == '#':
                pygame.draw.rect(screen, NAVY, (px,py,TILE,TILE))
            else:
                if (x,y) in pellets:
                    pygame.draw.circle(screen, WHITE, (px + TILE//2, py + TILE//2), 3)
                if (x,y) in power_pellets:
                    pygame.draw.circle(screen, WHITE, (px + TILE//2, py + TILE//2), 7)

def check_collisions():
    global ghosts_eaten_in_power
    if not player.alive: return
    for g in ghosts:
        dist = math.hypot(player.px - g.px, player.py - g.py)
        if dist < TILE * 0.6:
            if g.mode == "frightened" and not g.dead:
                score_table = [200,400,800,1600]
                pts = score_table[min(ghosts_eaten_in_power, 3)]
                player.score += pts
                ghosts_eaten_in_power += 1
                g.kill_and_become_eyes()
            elif g.mode != "eyes" and not g.dead:
                player.die()
                for i,gg in enumerate(ghosts):
                    sx,sy = ghost_starts[i]
                    cx,cy = tile_center_pixel(sx,sy)
                    gg.px, gg.py = float(cx), float(cy)
                    gg.tx, gg.ty = sx, sy
                    gg.dx, gg.dy = 0,0
                    gg.moving = False
                    gg.warping = False
                    gg.warp_target = None
                    gg.mode = "scatter"
                    gg.dead = False
                    gg.fright_timer = 0
                break

def reset_level():
    global pellets, power_pellets
    pellets = set()
    power_pellets = set()
    for y,row in enumerate(MAZE):
        for x,ch in enumerate(row):
            if ch == '.': pellets.add((x,y))
            elif ch == 'o': power_pellets.add((x,y))

reset_level()
ghosts_eaten_in_power = 0
set_global_mode(MODE_SEQUENCE[mode_index][0])

# ========== MAIN LOOP ==========
running = True
game_over = False
win = False
paused = False

while running:
    dt = clock.tick(FPS)
    for ev in pygame.event.get():
        if ev.type == pygame.QUIT:
            running = False
        elif ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_ESCAPE:
                running = False
            elif ev.key == pygame.K_p:
                paused = not paused
            elif ev.key in (pygame.K_LEFT, pygame.K_a):
                player.queue_dir(-1,0)
            elif ev.key in (pygame.K_RIGHT, pygame.K_d):
                player.queue_dir(1,0)
            elif ev.key in (pygame.K_UP, pygame.K_w):
                player.queue_dir(0,-1)
            elif ev.key in (pygame.K_DOWN, pygame.K_s):
                player.queue_dir(0,1)
            elif ev.key == pygame.K_r and (game_over or win):
                reset_level()
                player = Pacman(pac_start)
                ghosts = [Ghost(ghost_starts[i], i) for i in range(4)]
                game_over = False; win = False
                mode_index = 0; mode_timer = MODE_SEQUENCE[mode_index][1] * FPS
                set_global_mode(MODE_SEQUENCE[mode_index][0])

    if paused:
        draw_maze()
        for g in ghosts: g.draw(screen)
        player.draw(screen)
        draw_text(screen, "PAUSED - press P to resume", SCREEN_W//2 - 140, SCREEN_H//2)
        pygame.display.flip()
        continue

    if not game_over and not win:
        # mode timer
        mode_timer -= 1
        if mode_timer <= 0:
            mode_index = (mode_index + 1) % len(MODE_SEQUENCE)
            mode_name, sec = MODE_SEQUENCE[mode_index]
            mode_timer = sec * FPS
            set_global_mode(mode_name)

        # player update
        if not player.alive:
            player.respawn_timer -= 1
            if player.respawn_timer <= 0:
                if player.lives > 0:
                    player.respawn()
                else:
                    game_over = True
        else:
            player.update()
            player.pick_up()

        # ghosts update
        blinky_tile = (ghosts[0].tx, ghosts[0].ty) if ghosts else None
        for g in ghosts:
            g.update(player, blinky_tile)

        check_collisions()

        if not pellets and not power_pellets:
            win = True

    # draw
    draw_maze()
    for g in ghosts: g.draw(screen)
    player.draw(screen)
    draw_text(screen, f"Score: {player.score}", 10, SCREEN_H - 36)
    draw_text(screen, f"Lives: {'❤'*player.lives}", SCREEN_W - 200, SCREEN_H - 36, YELLOW)

    if game_over:
        draw_text(screen, "GAME OVER - Press R to restart", SCREEN_W//2 - 160, SCREEN_H//2)
    if win:
        draw_text(screen, "YOU WIN! - Press R to play again", SCREEN_W//2 - 160, SCREEN_H//2)

    pygame.display.flip()

pygame.quit()
sys.exit()

