import random
import sqlite3
from pathlib import Path
import pygame

# ---- настройки ----
WIDTH, HEIGHT = 800, 600
CELL = 32
TIME_LIMIT = 60

# ширина и высота должны быть нечётными, иначе dfs-генератор ломается
MAZE_WIDTH, MAZE_HEIGHT = 21, 13

TRAPS, FRUITS = 11, 18

# цвета подбирал, чтобы глаза не уставали от долгой игры
BG = (20, 28, 43)
WALL = (72, 93, 132)
FLOOR = (37, 52, 72)
WHITE = (245, 247, 255)
GOLD = (255, 206, 72)
GREEN = (99, 225, 149)
RED = (251, 91, 108)

# храним рядом со скриптом, чтобы можно было запускать откуда угодно
DB_PATH = Path(__file__).with_name('scores.sqlite3')


class ScoreDatabase:
    """
    Хранилище результатов. Сначала в таблице была ещё колонка difficulty,
    но я её выпилил — миграцию оставил, чтобы старые базы не отваливались.
    """
    def __init__(self, path=DB_PATH):
        self.path = path
        with sqlite3.connect(path) as con:
            columns = {row[1] for row in con.execute('PRAGMA table_info(results)')}
            if 'difficulty' in columns:
                # легаси-таблица из первой версии игры, где были уровни сложности
                con.execute('''CREATE TABLE results_new (
                    id INTEGER PRIMARY KEY, nickname TEXT NOT NULL,
                    points INTEGER NOT NULL, won INTEGER NOT NULL,
                    played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
                con.execute('''INSERT INTO results_new (id, nickname, points, won, played_at)
                    SELECT id, nickname, points, won, played_at FROM results''')
                con.execute('DROP TABLE results')
                con.execute('ALTER TABLE results_new RENAME TO results')
            else:
                con.execute('''CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY, nickname TEXT NOT NULL,
                    points INTEGER NOT NULL, won INTEGER NOT NULL,
                    played_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')

    def add_result(self, nickname, points, won):
        with sqlite3.connect(self.path) as con:
            con.execute('INSERT INTO results (nickname, points, won) VALUES (?, ?, ?)',
                        (nickname, points, int(won)))

    def top_five(self):
        # MAX(points), а не последняя партия — рекорд игрока интереснее
        with sqlite3.connect(self.path) as con:
            return con.execute('''SELECT nickname, MAX(points) AS best
                FROM results GROUP BY nickname
                ORDER BY best DESC, nickname COLLATE NOCASE LIMIT 5''').fetchall()


class Player:
    def __init__(self):
        self.cell = (1, 1)
        self.frozen_until = 0.0

    def move(self, dx, dy, maze, elapsed):
        if elapsed < self.frozen_until:
            return False
        x, y = self.cell
        next_cell = x + dx, y + dy
        if maze.is_open(*next_cell):
            self.cell = next_cell
            return True
        return False

    def draw(self, screen, offset):
        x, y = self.cell
        cx = offset + x * CELL + CELL // 2
        cy = 90 + y * CELL + CELL // 2

        spines = [
            (-13, 5), (-14, -1), (-10, 0), (-12, -7),
            (-7, -5), (-7, -12), (-2, -8), (0, -13),
            (4, -8), (7, -11), (9, -5), (11, -3),
            (9, 6),
        ]
        pygame.draw.polygon(
            screen,
            (95, 62, 46),
            [(cx + dx, cy + dy) for dx, dy in spines],
        )

        pygame.draw.ellipse(screen, (198, 145, 86), (cx - 12, cy - 5, 24, 16))
        pygame.draw.polygon(
            screen,
            GOLD,
            [(cx + 5, cy - 3), (cx + 14, cy + 5), (cx + 5, cy + 9)],
        )
        pygame.draw.circle(screen, BG, (cx + 7, cy + 1), 2)
        pygame.draw.circle(screen, BG, (cx + 14, cy + 5), 2)


class Maze:
    def __init__(self, width, height, traps_count, fruit_count):
        self.width, self.height = width, height
        # 1 — стена, 0 — коридор; начинаем с полностью заполненной сетки
        self.grid = [[1] * width for _ in range(height)]
        self.grid[1][1] = 0

        # обычный DFS-генератор, ходим через клетку, чтобы стены оставались толщиной 1
        stack = [(1, 1)]
        while stack:
            x, y = stack[-1]
            directions = [(2, 0), (-2, 0), (0, 2), (0, -2)]
            random.shuffle(directions)
            for dx, dy in directions:
                nx, ny = x + dx, y + dy
                if 0 < nx < width - 1 and 0 < ny < height - 1 and self.grid[ny][nx]:
                    self.grid[y + dy // 2][x + dx // 2] = 0
                    self.grid[ny][nx] = 0
                    stack.append((nx, ny))
                    break
            else:
                stack.pop()

        self.exit = (width - 2, height - 2)

        # все свободные клетки, кроме старта и выхода
        # TODO: ловушки иногда ставятся прямо у входа — можно фильтровать по манхэттену
        cells = [(x, y) for y in range(1, height - 1) for x in range(1, width - 1)
                 if self.is_open(x, y) and (x, y) not in ((1, 1), self.exit)]
        random.shuffle(cells)
        self.traps = set(cells[:traps_count])
        self.fruits = set(cells[traps_count:traps_count + fruit_count])

    def is_open(self, x, y):
        return 0 <= x < self.width and 0 <= y < self.height and self.grid[y][x] == 0


class Game:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption('Фруктовый лабиринт')
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont('dejavusans', 23)
        self.small = pygame.font.SysFont('dejavusans', 18)
        self.large = pygame.font.SysFont('dejavusans', 40, bold=True)
        self.db = ScoreDatabase()

        self.state = 'name'
        self.nickname = ''
        self.points = 0
        self.elapsed = 0.0
        self.won = False
        self.notice = ''

    def text(self, label, y, font=None, color=WHITE):
        image = (font or self.font).render(label, True, color)
        self.screen.blit(image, image.get_rect(center=(WIDTH // 2, y)))

    def start(self):
        self.maze = Maze(MAZE_WIDTH, MAZE_HEIGHT, TRAPS, FRUITS)
        self.player = Player()
        self.points = 0
        self.elapsed = 0.0
        self.won = False
        self.notice = 'Найдите зелёный выход! Фрукты необязательны.'
        self.state = 'playing'
        # print('traps:', self.maze.traps)  # дебаг генерации, не удалять

    def finish(self, won):
        # защита от двойного вызова: например, если время кончилось ровно на клетке выхода
        if self.state != 'playing':
            return
        self.won = won
        self.db.add_result(self.nickname, self.points, won)
        self.state = 'result'

    def step(self, dx, dy):
        if self.state != 'playing':
            return
        if not self.player.move(dx, dy, self.maze, self.elapsed):
            return

        position = self.player.cell
        if position in self.maze.fruits:
            self.maze.fruits.remove(position)
            self.points += 1
            self.notice = '+1 очко за фрукт!'
        if position in self.maze.traps:
            self.maze.traps.remove(position)
            # 5 секунд штрафа; таймер специально не останавливаем — так честнее
            self.player.frozen_until = self.elapsed + 5
            self.notice = 'Ловушка! Ожидание 5 секунд; таймер идёт.'
        if position == self.maze.exit:
            self.finish(True)

    def handle_event(self, event):
        if event.type != pygame.KEYDOWN:
            return

        if self.state == 'name':
            if event.key == pygame.K_BACKSPACE:
                self.nickname = self.nickname[:-1]
            elif event.key == pygame.K_RETURN and self.nickname.strip():
                self.nickname = self.nickname.strip()
                self.state = 'rules'
            elif event.unicode and event.unicode.isprintable() and len(self.nickname) < 20:
                self.nickname += event.unicode

        elif self.state == 'rules':
            if event.key == pygame.K_RETURN:
                self.start()

        elif self.state == 'playing':
            movements = {pygame.K_UP: (0, -1), pygame.K_DOWN: (0, 1),
                         pygame.K_LEFT: (-1, 0), pygame.K_RIGHT: (1, 0)}
            if event.key in movements:
                self.step(*movements[event.key])

        elif self.state == 'result':
            if event.key == pygame.K_RETURN:
                self.start()

        # с экранов правил и результатов можно вернуться к вводу имени
        # (на playing Esc работает как выход из игры, см. run)
        if event.key == pygame.K_ESCAPE and self.state in ('rules', 'result'):
            self.state = 'name'
            self.nickname = ''

    def update(self, dt):
        if self.state == 'playing':
            self.elapsed += dt
            if self.elapsed >= TIME_LIMIT:
                self.finish(False)

    def draw(self):
        self.screen.fill(BG)

        if self.state == 'name':
            self.text('ФРУКТОВЫЙ ЛАБИРИНТ', 150, self.large, GOLD)
            self.text('Введите никнейм (до 20 символов):', 255)
            self.text(self.nickname + '|', 310, self.large, GREEN)
            self.text('Enter — продолжить', 395, self.small)

        elif self.state == 'rules':
            self.text('Правила игры', 90, self.large, GOLD)
            self.text(f'Игрок: {self.nickname}', 155)
            self.text('Стрелки — двигать ёжика по коридорам лабиринта.', 205, self.small)
            self.text('Доберитесь до зелёного выхода за 1 минуту.', 240, self.small)
            self.text('Фрукты необязательны: каждый приносит 1 очко.', 275, self.small)
            self.text('Не успели к выходу — поражение.', 310, self.small, RED)
            self.text('Жёлтые ловушки задерживают ёжика на 5 секунд.', 375, self.small)
            self.text('Пока ёжик стоит в ловушке, время продолжает идти.', 410, self.small)
            self.text('Enter — начать; Esc — сменить имя', 515, self.small, GREEN)

        elif self.state == 'playing':
            # ceil, чтобы не показывать 0, пока время ещё есть
            remaining = max(0, int(TIME_LIMIT - self.elapsed + 0.999))
            self.text(f'{self.nickname}  |  Фрукты: {self.points}  |  {remaining} с', 32)

            offset = (WIDTH - self.maze.width * CELL) // 2
            for y, row in enumerate(self.maze.grid):
                for x, wall in enumerate(row):
                    rect = pygame.Rect(offset + x * CELL, 90 + y * CELL, CELL, CELL)
                    pygame.draw.rect(self.screen, WALL if wall else FLOOR, rect)
                    pygame.draw.rect(self.screen, BG, rect, 1)  # тонкая сетка между клетками

            for cells, color in ((self.maze.fruits, RED), (self.maze.traps, GOLD), ({self.maze.exit}, GREEN)):
                for x, y in cells:
                    pygame.draw.circle(self.screen, color,
                                       (offset + x * CELL + 16, 90 + y * CELL + 16), 9)

            self.player.draw(self.screen, offset)

            if self.elapsed < self.player.frozen_until:
                self.notice = f'Ловушка: осталось {self.player.frozen_until - self.elapsed:.1f} с'
            self.text(self.notice, 558, self.small, GOLD)

        else:
            self.text('ПОБЕДА!' if self.won else 'ВРЕМЯ ВЫШЛО',
                      85, self.large, GREEN if self.won else RED)
            self.text(f'{self.nickname}: {self.points} очков', 145)
            self.text('Общий топ-5 игроков по лучшему результату', 208, self.small, GOLD)
            for index, (name, score) in enumerate(self.db.top_five(), 1):
                self.text(f'{index}. {name} — {score}', 230 + index * 47)
            self.text('Enter — новая игра; Esc — сменить имя', 535, self.small)

        pygame.display.flip()

    def run(self):
        while True:
            # ограничение dt — на случай лагов системы или выхода из спящего режима,
            # иначе за один тик можно проскочить таймер и всю ловушку разом
            dt = min(self.clock.tick(60) / 1000, 0.1)
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (
                        event.type == pygame.KEYDOWN and
                        event.key == pygame.K_ESCAPE and
                        self.state == 'playing'):
                    pygame.quit()
                    return
                self.handle_event(event)
            self.update(dt)
            self.draw()


if __name__ == '__main__':
    Game().run()