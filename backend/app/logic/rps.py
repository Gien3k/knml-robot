"""Papier, kamień, nożyce z robotem: rozpoznanie gestu z 21 punktów dłoni i przebieg gry.

Rozpoznanie (classify) liczy na punktach MediaPipe Hands, które i tak przychodzą z kamery:
palec jest wyprostowany, gdy jego czubek leży wyraźnie dalej od nadgarstka niż staw
środkowy, a sam palec jest prawie prosty; zgięty, gdy czubek wraca w stronę dłoni.
Kciuk jest pomijany (kamera myli go najczęściej), liczą się cztery palce:
  kamień  = cztery zgięte,  papier = cztery wyprostowane,
  nożyce  = wskazujący i środkowy wyprostowane, serdeczny i mały zgięte.
Pozycja w pół drogi (palec ani prosty, ani zgięty) = brak gestu – lepiej poczekać klatkę.

Przebieg gry (RpsGame) – moduł czysty: czas podawany jawnie, testy bez kamery i wątków.
  intro -> [countdown -> read -> reveal] x rundy -> final        (read bez gestu -> miss)
  countdown: "Papier" "Kamień" "Nożyce" co beat_s, potem "Teraz!" (read)
  read:      gest ODWIEDZAJĄCEGO jest zamrażany, gdy trzyma się min_frames klatek
             i hold_s sekund liczonych od "Teraz!" (pompowanie pięścią przed "Teraz!" się nie liczy)
  uczciwe rundy: robot losuje ruch na początku odliczania, zanim zobaczy dłoń,
  ostatnia runda (cheat_last): robot NIE losuje – patrzy na gest i wybiera ten, który wygrywa.
  Po grze przyznaje się i pokazuje, ile zajęło mu rozpoznanie.
"""
from __future__ import annotations

import datetime
import math
import random
from collections import deque
from dataclasses import dataclass, field

ROCK, PAPER, SCISSORS = "rock", "paper", "scissors"
MOVES = (PAPER, ROCK, SCISSORS)
BEATS = {ROCK: SCISSORS, SCISSORS: PAPER, PAPER: ROCK}       # klucz wygrywa z wartością
WINNING_MOVE = {loser: winner for winner, loser in BEATS.items()}   # co wygrywa z danym ruchem
MOVE_PL = {ROCK: "Kamień", PAPER: "Papier", SCISSORS: "Nożyce"}
MOVE_ASCII = {ROCK: "KAMIEN", PAPER: "PAPIER", SCISSORS: "NOZYCE"}

# indeksy MediaPipe: (podstawa, staw środkowy, staw końcowy, czubek)
FINGERS = ((5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20))
THUMB = (1, 2, 3, 4)
EXTENDED, FOLDED = 1, 0


def _pts(landmarks, aspect: float) -> list[tuple[float, float]] | None:
    """Punkty 0..1 obrazu -> jednakowa skala w obu osiach (x mnożony przez proporcje kadru)."""
    if not landmarks or len(landmarks) != 21:
        return None
    try:
        return [(float(p[0]) * aspect, float(p[1])) for p in landmarks]
    except (TypeError, ValueError, IndexError):
        return None


def _d(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _straightness(p, idx) -> float:
    """Cięciwa / długość łamanej palca: 1 = prosty, ~0,3 = zwinięty."""
    path = sum(_d(p[idx[k]], p[idx[k + 1]]) for k in range(len(idx) - 1))
    return _d(p[idx[0]], p[idx[-1]]) / path if path > 1e-9 else 0.0


def finger_state(p, f) -> int | None:
    wrist = p[0]
    mcp, pip, _dip, tip = (p[i] for i in f)
    reach = _d(wrist, tip) / max(1e-9, _d(wrist, pip))
    straight = _straightness(p, f)
    if reach > 1.12 and straight > 0.72:
        return EXTENDED
    if reach < 1.0 or straight < 0.55:
        return FOLDED
    return None


# wzorce gestów: 1 = palec wyprostowany, 0 = zgięty (wskazujący, środkowy, serdeczny, mały)
PATTERNS = {PAPER: (1, 1, 1, 1), ROCK: (0, 0, 0, 0), SCISSORS: (1, 1, 0, 0)}


def decide(ext: list[float]) -> str | None:
    """Gest z "wyprostowania" czterech palców (0..1). Dopasowanie do wzorca jako całość:
    jeden palec zgięty w połowie (rozluźniona dłoń, nie wysunięta mocno do kamery) nie psuje gestu,
    ale dłoń niejednoznaczna (np. wszystkie palce w połowie) dalej daje None."""
    scores = []
    for g, pat in PATTERNS.items():
        m = [e if want else 1.0 - e for e, want in zip(ext, pat)]
        scores.append((sum(m) / 4.0, min(m), g))
    scores.sort(reverse=True)
    (mean1, min1, g1), (mean2, _, _) = scores[0], scores[1]
    if mean1 >= 0.75 and min1 >= 0.3 and mean1 - mean2 >= 0.2:
        return g1
    return None


def finger_ext(p, f) -> float:
    """Wyprostowanie palca z punktów na obrazie: 1 = prosty i daleko od nadgarstka, 0 = zwinięty."""
    wrist = p[0]
    _mcp, pip, _dip, tip = (p[i] for i in f)
    reach = _d(wrist, tip) / max(1e-9, _d(wrist, pip))
    straight = _straightness(p, f)
    return max(0.0, min(1.0, (reach - 1.0) / 0.12, (straight - 0.55) / 0.17))


def classify(landmarks, aspect: float = 16 / 9) -> str | None:
    """Gest z 21 punktów (0..1 obrazu): rock | paper | scissors | None (niepewny / inny)."""
    p = _pts(landmarks, aspect)
    if p is None or _d(p[0], p[9]) < 0.01:          # brak dłoni / zdegenerowane punkty
        return None
    return decide([finger_ext(p, f) for f in FINGERS])


def is_thumbs_up(landmarks, aspect: float = 16 / 9) -> bool:
    """Kciuk w górę: cztery palce zgięte, kciuk prosty i jego czubek najwyżej ze wszystkich punktów."""
    p = _pts(landmarks, aspect)
    if p is None or _d(p[0], p[9]) < 0.01:
        return False
    if any(finger_state(p, f) != FOLDED for f in FINGERS):
        return False
    palm = _d(p[0], p[9])
    tip = p[THUMB[-1]]
    others = min(q[1] for i, q in enumerate(p) if i not in THUMB and i != 0)
    return (_straightness(p, THUMB) > 0.8 and tip[1] < others - 0.12 * palm
            and _d(p[THUMB[0]], tip) > 0.45 * palm)


# ------------------------------------------------------------------ rozpoznanie w 3D (dokładniejsze)
def _bend(a, b, c) -> float:
    """Zgięcie w stawie b [°]: 0 = prosto, 90 = pod kątem prostym."""
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - b[i] for i in range(3)]
    nu = math.sqrt(sum(x * x for x in u))
    nv = math.sqrt(sum(x * x for x in v))
    if nu < 1e-9 or nv < 1e-9:
        return 0.0
    cos = max(-1.0, min(1.0, sum(u[i] * v[i] for i in range(3)) / (nu * nv)))
    return math.degrees(math.acos(cos))


def _world(world) -> list | None:
    if not world or len(world) != 21:
        return None
    try:
        w = [(float(p[0]), float(p[1]), float(p[2])) for p in world]
    except (TypeError, ValueError, IndexError):
        return None
    span = math.sqrt(sum((w[9][i] - w[0][i]) ** 2 for i in range(3)))
    return w if span > 0.02 else None                  # nadgarstek -> środkowy staw > 2 cm


def finger_state3d(w, f) -> int | None:
    """Palec w 3D: suma zgięć w stawie środkowym i końcowym (niezależna od obrotu dłoni)."""
    mcp, pip, dip, tip = (w[i] for i in f)
    bend = _bend(mcp, pip, dip) + _bend(pip, dip, tip)
    if bend < 55.0:
        return EXTENDED
    if bend > 100.0:
        return FOLDED
    return None


def finger_ext3d(w, f) -> float:
    """Wyprostowanie palca w 3D: 1 przy zgięciu w stawach do 55°, 0 od 100° (płynnie pomiędzy)."""
    mcp, pip, dip, tip = (w[i] for i in f)
    bend = _bend(mcp, pip, dip) + _bend(pip, dip, tip)
    return max(0.0, min(1.0, (100.0 - bend) / 45.0))


def classify3d(world) -> str | None:
    """Gest z punktów 3D w metrach (MediaPipe "world landmarks"; x w prawo, y w dół)."""
    w = _world(world)
    if w is None:
        return None
    return decide([finger_ext3d(w, f) for f in FINGERS])


def hand_ext(hand: dict | None) -> list[float] | None:
    """Wyprostowanie czterech palców (do diagnostyki rozpoznawania): z 3D, jeśli są, inaczej z obrazu."""
    if not hand:
        return None
    w = _world(hand.get("world"))
    if w is not None:
        return [round(finger_ext3d(w, f), 2) for f in FINGERS]
    p = _pts(hand.get("landmarks"), 16 / 9)
    if p is None or _d(p[0], p[9]) < 0.01:
        return None
    return [round(finger_ext(p, f), 2) for f in FINGERS]


def is_thumbs_up3d(world) -> bool:
    w = _world(world)
    if w is None or any(finger_state3d(w, f) != FOLDED for f in FINGERS):
        return False
    thumb_bend = _bend(w[1], w[2], w[3]) + _bend(w[2], w[3], w[4])
    d = [w[4][i] - w[2][i] for i in range(3)]
    n = math.sqrt(sum(x * x for x in d)) or 1.0
    highest = w[4][1] <= min(p[1] for i, p in enumerate(w) if i not in THUMB)
    return thumb_bend < 45.0 and d[1] / n < -0.6 and highest


def classify_hand(hand: dict | None) -> str | None:
    """Najlepsze dostępne rozpoznanie: punkty 3D (jeśli są), inaczej punkty na obrazie."""
    if not hand:
        return None
    if hand.get("world"):
        g = classify3d(hand["world"])
        if g is not None or _world(hand["world"]) is not None:
            return g
    return classify(hand.get("landmarks"))


def thumbs_up_hand(hand: dict | None) -> bool:
    if not hand:
        return False
    if hand.get("world") and _world(hand["world"]) is not None:
        return is_thumbs_up3d(hand["world"])
    return is_thumbs_up(hand.get("landmarks"))


class GestureVote:
    """Głosowanie z ostatnich klatek: gest zmienia się dopiero, gdy potwierdzi go większość
    (pojedyncza zła klatka nie szarpie kołami ani nie zmienia ruchu w grze)."""

    def __init__(self, n: int = 5, need: int = 3) -> None:
        self.n, self.need = n, need
        self.labels: list[str | None] = []
        self.last_t = None
        self.out: str | None = None

    def reset(self) -> None:
        self.labels, self.last_t, self.out = [], None, None

    def update(self, t, label: str | None) -> str | None:
        if t == self.last_t:
            return self.out
        self.last_t = t
        self.labels = (self.labels + [label])[-self.n:]
        counts: dict[str, int] = {}
        for x in self.labels:
            if x:
                counts[x] = counts.get(x, 0) + 1
        top = max(counts, key=counts.get) if counts else None
        if top is not None and counts[top] >= self.need:
            self.out = top
        elif self.out is not None and self.out not in self.labels[-3:]:
            self.out = None
        return self.out


def result(human: str, robot: str) -> str:
    if human == robot:
        return "draw"
    return "human" if BEATS[human] == robot else "robot"


# =============================================================================== przebieg gry
OFF, INTRO, COUNTDOWN, READ, REVEAL, MISS, FINAL, ABORT = (
    "off", "intro", "countdown", "read", "reveal", "miss", "final", "abort")
ACTIVE = {INTRO, COUNTDOWN, READ, REVEAL, MISS, FINAL, ABORT}
WATCHING = {COUNTDOWN, READ, REVEAL, MISS}        # tu brak dłoni przez dłuższą chwilę przerywa grę


@dataclass
class RpsParams:
    rounds: int = 3                  # bez "wins": tyle rund; z "wins": najwyżej tyle rund (bezpiecznik na remisy)
    wins: int = 0                    # >0: gra do tylu wygranych (remisy się nie liczą), np. 3 = best of 5
    cheat_last: bool = True          # ostatnia runda: robot patrzy na dłoń i wybiera ruch wygrywający
    intro_s: float = 3.4             # wejście areny + "VS" + trzy ruchy do wyboru
    beat_s: float = 0.8              # "Papier" - "Kamień" - "Nożyce" - "Teraz!" (spokojnie dla kogoś, kto gra pierwszy raz)
    read_s: float = 1.8              # ile czekamy na gest po "Teraz!"
    hold_s: float = 0.1              # gest musi się utrzymać tyle (od "Teraz!")...
    min_frames: int = 3              # ...i przez tyle klatek kamery
    reveal_s: float = 3.0
    miss_s: float = 2.2
    final_s: float = 15.0            # wynik (~3,5 s) + powtórka ostatniej rundy w zwolnionym tempie
    choice_s: float = 25.0           # potem przyciski "Zagraj jeszcze raz" / "Menu" najwyżej tyle...
    choice_idle_s: float = 5.0       # ...albo do chwili, gdy dłoni nie ma przez tyle sekund (-> menu)
    abort_s: float = 3.0
    warmup: bool = True              # przed rundą 1 rozgrzewka bez punktów: "pokaż gest dopiero na Teraz!"
    forfeit_after: int = 2           # tyle razy bez gestu w tej samej rundzie -> rundę wygrywa robot (walkower)
    no_hand_abort_s: float = 7.0
    start_hold_s: float = 0.5        # tyle trzeba trzymać kciuk w górę
    cooldown_s: float = 3.0          # po grze: chwila, zanim kciuk znów ją uruchomi
    wheels: bool = True              # koła "pompują" w rytm odliczania i tańczą po rundzie
    wheel_speed: float = 0.15        # [m/s]

    @classmethod
    def from_settings(cls, s) -> "RpsParams":
        return cls(rounds=max(1, int(s.rps_rounds)), wins=max(0, int(s.rps_wins)),
                   cheat_last=s.rps_cheat_last, wheels=s.rps_wheels,
                   warmup=s.rps_warmup,
                   wheel_speed=max(0.0, min(s.rps_wheel_speed, 0.6 * s.max_speed)))


@dataclass
class Round:
    human: str | None                # None = gracz nie pokazał gestu (walkower dla robota)
    robot: str
    result: str
    cheat: bool
    rec_s: float | None = None       # ile robot potrzebował od pierwszej klatki z gestem do decyzji
    since_s: float | None = None     # kiedy gest stał się widoczny, licząc od "Teraz!" (do powtórki)


@dataclass
class DayStats:
    day: str = field(default_factory=lambda: datetime.date.today().isoformat())
    games: int = 0
    robot: int = 0
    human: int = 0
    draw: int = 0

    def add(self, winner: str) -> None:
        today = datetime.date.today().isoformat()
        if today != self.day:
            self.day, self.games, self.robot, self.human, self.draw = today, 0, 0, 0, 0
        self.games += 1
        setattr(self, winner, getattr(self, winner) + 1)

    def to_dict(self) -> dict:
        return {"games": self.games, "robot": self.robot, "human": self.human, "draw": self.draw}


class RpsGame:
    WORDS = ("Papier", "Kamień", "Nożyce", "Teraz!")

    def __init__(self, params: RpsParams | None = None, rng: random.Random | None = None) -> None:
        self.p = params or RpsParams()
        self.rng = rng or random.Random()
        self.stats = DayStats()
        self.phase = OFF
        self.t_phase = 0.0
        self.cooldown_until = 0.0
        self._reset_match()
        self._thumb_since: float | None = None
        self._last_obs_t: float | None = None
        self.last_hand_t = 0.0
        self.live: str | None = None            # co kamera widzi teraz (podpowiedź na ekranie)
        self.debug: deque = deque(maxlen=400)   # klatki z okna "Teraz!": gest i wyprostowanie palców (/api/game/debug)
        self.match_no = 0                       # licznik meczów (numer ekranu w control.py)

    # ------------------------------------------------------------------ sterowanie z zewnątrz
    @property
    def active(self) -> bool:
        return self.phase in ACTIVE

    def start(self, now: float) -> None:
        self.match_no += 1
        self._reset_match()
        self.last_hand_t = now
        self._enter(INTRO, now)

    def stop(self, now: float) -> None:
        if self.active:
            self.phase = OFF
            self.cooldown_until = now + self.p.cooldown_s

    # ------------------------------------------------------------------ krok (20 Hz)
    def update(self, now: float, hand: dict | None, available: bool = True, can_start: bool = True) -> None:
        """hand: obserwacja z kamery ({"t", "landmarks", ...}) albo None.
        available: kamera daje punkty dłoni; can_start: kciuk może teraz uruchomić grę (robot nie jedzie)."""
        if not available:
            if self.active:
                self.phase = OFF
            self._thumb_since = None
            self.live = None
            return
        has = bool(hand) and bool(hand.get("landmarks") or hand.get("world"))
        fresh = has and hand.get("t") != self._last_obs_t
        if has:
            self.last_hand_t = now
        if fresh:
            self._last_obs_t = hand.get("t")
        self.live = classify_hand(hand) if has else None        # 3D, jeśli kamera je daje

        if not self.active:
            self._watch_thumb(now, hand if (can_start and has) else None)
            return
        if self.phase in WATCHING and now - self.last_hand_t > self.p.no_hand_abort_s:
            self._enter(ABORT, now)
            return

        el = now - self.t_phase
        p = self.p
        if self.phase == INTRO and el >= p.intro_s:
            self._begin_round(now)
        elif self.phase == COUNTDOWN and el >= 3 * p.beat_s:
            self._enter(READ, now)
            self._votes = {}
        elif self.phase == READ:
            if fresh:
                self._observe(float(hand["t"]), now, hand)
            if self.phase == READ and el >= p.read_s:
                best = self._best_vote()
                if best is not None:
                    # koniec okna: gest widziany najczęściej (co najmniej 2 klatki) zamiast "nie widzę"
                    self.debug.append({"round": self.round_no, "warmup": self.warmup, "end": best[0]})
                    self._lock(best[0], best[1], now)
                    return
                self.debug.append({"round": self.round_no, "warmup": self.warmup, "miss": True})
                # brak gestu: najpierw przypomnienie i powtórka rundy, za drugim razem runda dla robota
                # (w rozgrzewce bez walkowera: po przypomnieniu gra po prostu rusza)
                self.misses += 1
                if self.warmup or self.misses < p.forfeit_after:
                    self._enter(MISS, now)
                else:
                    self._forfeit(now)
        elif self.phase == REVEAL and el >= p.reveal_s:
            if self.warmup:
                self.warmup = False                                  # po rozgrzewce: runda 1 na punkty
                self._begin_round(now)
            elif self.match_over:
                self._finish(now)
            else:
                self._begin_round(now)
        elif self.phase == MISS and el >= p.miss_s:
            if self.warmup and self.misses >= p.forfeit_after:
                self.warmup = False                                  # dwa razy bez gestu w rozgrzewce: gramy
                self._begin_round(now)
            else:
                self._begin_round(now, same=True)
        elif self.phase == FINAL and (el >= p.final_s + p.choice_s or (
                el >= p.final_s and now - self.last_hand_t > p.choice_idle_s)):
            # gracz nie wybrał "Zagraj jeszcze raz" ani "Menu" (odszedł) -> ekran startowy
            self.phase = OFF
            self.cooldown_until = now + p.cooldown_s
        elif self.phase == ABORT and el >= p.abort_s:
            self.phase = OFF
            self.cooldown_until = now + p.cooldown_s

    # ------------------------------------------------------------------ wnętrze
    def _reset_match(self) -> None:
        self.history: list[Round] = []
        self.misses = 0
        self.robot_pick: str | None = None
        self.human: str | None = None
        self.robot: str | None = None
        self.outcome: str | None = None
        self.winner: str | None = None
        self._votes: dict[str, list] = {}
        self.warmup = self.p.warmup                  # runda próbna przed meczem (nie trafia do historii)

    def _enter(self, phase: str, now: float) -> None:
        self.phase = phase
        self.t_phase = now

    def _watch_thumb(self, now: float, hand) -> None:
        if now < self.cooldown_until or hand is None or not thumbs_up_hand(hand):
            self._thumb_since = None
            return
        if self._thumb_since is None:
            self._thumb_since = now
        elif now - self._thumb_since >= self.p.start_hold_s:
            self._thumb_since = None
            self.start(now)

    @property
    def round_no(self) -> int:
        if self.warmup:
            return 0                                                 # rozgrzewka
        return min(self.p.rounds, len(self.history) + (0 if self.phase in (REVEAL, FINAL) else 1))

    @property
    def match_over(self) -> bool:
        """Koniec meczu: ktoś ma "wins" wygranych (tryb do wygranych) albo rozegrano limit rund."""
        if self.p.wins:
            sc = self.score()
            if max(sc["human"], sc["robot"]) >= self.p.wins:
                return True
        return len(self.history) >= self.p.rounds

    @property
    def decider(self) -> bool:
        """Runda decydująca: przy grze do 3 wygranych to stan 2:2, inaczej ostatnia runda."""
        if self.warmup:
            return False
        if self.p.wins:
            sc = self.score()
            return sc["human"] == sc["robot"] == self.p.wins - 1 and len(self.history) < self.p.rounds
        return len(self.history) == self.p.rounds - 1

    @property
    def cheat_round(self) -> bool:
        # robot "podgląda" tylko w rundzie decydującej (przy 2:2); w innych rundach gra uczciwie
        return self.p.cheat_last and self.decider

    def _begin_round(self, now: float, same: bool = False) -> None:
        if not same:
            self.misses = 0
        self.human = self.robot = self.outcome = None
        # uczciwa runda: ruch robota losowany TERAZ, zanim zobaczy dłoń
        self.robot_pick = None if self.cheat_round else self.rng.choice(MOVES)
        self._enter(COUNTDOWN, now)

    def _observe(self, t: float, now: float, hand: dict | None = None) -> None:
        """Nowa klatka w fazie READ: głosowanie na gest pokazany od "Teraz!". Liczą się wszystkie
        klatki z gestem (nie muszą iść jedna po drugiej), więc pojedyncza zła klatka nie zeruje liczenia."""
        g = self.live
        t_go = self.t_phase
        if t < t_go:                                   # pięść z odliczania przed "Teraz!" się nie liczy
            return
        self.debug.append({"round": self.round_no, "warmup": self.warmup, "t": round(t - t_go, 2), "g": g,
                           "ext": hand_ext(hand)})
        v = self._votes
        if g is not None:
            v.setdefault(g, [0, t])[0] += 1
        if not v:
            return
        cheat = self.robot_pick is None
        # runda "robota": wystarczą 2 klatki – tu liczy się szybkość reakcji maszyny
        need, hold = (min(2, self.p.min_frames), 0.0) if cheat else (self.p.min_frames, self.p.hold_s)
        total = sum(n for n, _ in v.values())
        top = max(v, key=lambda k: v[k][0])
        n, since = v[top]
        if g == top and n >= need and n >= 0.6 * total and t - since >= hold:
            self._lock(top, since, now)

    def _best_vote(self) -> tuple[str, float] | None:
        """Gest widziany najczęściej w oknie (co najmniej 2 klatki i połowa klatek z gestem)."""
        v = self._votes
        if not v:
            return None
        total = sum(n for n, _ in v.values())
        top = max(v, key=lambda k: v[k][0])
        n, since = v[top]
        return (top, since) if n >= 2 and n >= 0.5 * total else None

    def _lock(self, human: str, since: float, now: float) -> None:
        """Gest zamrożony: ruch robota, wynik rundy, odsłona."""
        t_go = self.t_phase
        cheat = self.robot_pick is None
        self.debug.append({"round": self.round_no, "warmup": self.warmup, "lock": human})
        robot = WINNING_MOVE[human] if cheat else self.robot_pick
        rec = round(max(0.0, now - since), 3)
        self.human, self.robot = human, robot
        self.outcome = result(human, robot)
        if self.warmup:                                              # rozgrzewka: bez punktu
            self._enter(REVEAL, now)
            return
        self.history.append(Round(human, robot, self.outcome, cheat, rec if cheat else None,
                                  round(since - t_go, 3) if cheat else None))
        self._enter(REVEAL, now)

    def _forfeit(self, now: float) -> None:
        """Gracz dwa razy nie pokazał gestu: robot wygrywa rundę walkowerem, gra toczy się dalej."""
        cheat = self.robot_pick is None
        self.human, self.robot = None, self.robot_pick or self.rng.choice(MOVES)
        self.outcome = "robot"
        self.history.append(Round(None, self.robot, "robot", cheat))
        self._enter(REVEAL, now)

    def _finish(self, now: float) -> None:
        h = sum(r.result == "human" for r in self.history)
        r = sum(r.result == "robot" for r in self.history)
        self.winner = "human" if h > r else "robot" if r > h else "draw"
        self.stats.add(self.winner)
        self._enter(FINAL, now)

    # ------------------------------------------------------------------ dla ekranu i kół
    def score(self) -> dict:
        return {"human": sum(r.result == "human" for r in self.history),
                "robot": sum(r.result == "robot" for r in self.history)}

    def view(self, now: float, available: bool = True) -> dict:
        el = max(0.0, now - self.t_phase)
        beat = None
        if self.phase == COUNTDOWN:
            beat = min(2, int(el / self.p.beat_s))
        elif self.phase == READ:
            beat = 3
        cheat = next((r for r in self.history if r.cheat), None)
        dur = {INTRO: self.p.intro_s, COUNTDOWN: 3 * self.p.beat_s, READ: self.p.read_s,
               REVEAL: self.p.reveal_s, MISS: self.p.miss_s, FINAL: self.p.final_s,
               ABORT: self.p.abort_s}.get(self.phase)
        return {
            "active": self.active, "available": available, "phase": self.phase,
            "elapsed": round(el, 2), "duration": dur,
            "round": self.round_no, "rounds": self.p.rounds, "warmup": self.active and self.warmup,
            "wins": self.p.wins, "decider": self.active and self.phase in (COUNTDOWN, READ, MISS) and self.decider,
            "beat": beat, "beat_s": self.p.beat_s, "word": None if beat is None else self.WORDS[beat],
            "score": self.score(), "live": self.live,
            "human": self.human, "robot": self.robot, "result": self.outcome,
            "winner": self.winner if self.phase == FINAL else None,
            "history": [{"human": r.human, "robot": r.robot, "result": r.result, "cheat": r.cheat}
                        for r in self.history],
            "cheat": None if cheat is None or self.phase != FINAL else
            {"human": cheat.human, "robot": cheat.robot, "rec_s": cheat.rec_s, "since_s": cheat.since_s},
            # kciuk w górę trzymany: 0..1 (pierścień wokół dłoni na ekranie)
            "start_progress": 0.0 if self.active or self._thumb_since is None else
            round(min(1.0, (now - self._thumb_since) / max(1e-6, self.p.start_hold_s)), 2),
            "stats": self.stats.to_dict(),
        }

    def wheels(self, now: float) -> tuple[float, float] | None:
        """Ruch kół w grze (robot stoi na podnośniku): None = gra nie steruje kołami.

        Spokojnie, bez szybkich zmian kierunku (ochrona silników): w odliczaniu koła
        "nabierają obrotów" do przodu, po wygranej robota powolny obrót w miejscu,
        po przegranej krótkie cofnięcie. Wszystko i tak przechodzi przez łagodny napęd."""
        if not self.p.wheels or not self.active:
            return None
        a = self.p.wheel_speed
        el = now - self.t_phase
        if self.phase == COUNTDOWN:
            beat = min(2, int(el / self.p.beat_s))
            v = a * (0.4 + 0.2 * beat)
            return v, v
        if self.phase == READ:
            return a, a
        if self.phase == REVEAL:
            if self.outcome == "robot" and el < 1.6:
                return a, -a                                       # obrót w miejscu
            if self.outcome == "human" and el < 1.2:
                return -0.6 * a, -0.6 * a                          # "smutne" cofnięcie
            return 0.0, 0.0
        if self.phase == FINAL and self.winner == "robot" and el < 2.0:
            return a, -a
        return 0.0, 0.0

    def oled(self) -> list[str] | None:
        """4 linie na mały ekran łazika (0,91", tylko ASCII); None = ekran domyślny firmware'u."""
        if not self.active:
            return None
        sc = self.score()
        line3 = f"TY {sc['human']} : {sc['robot']} ROBOT"
        if self.phase == INTRO:
            return ["CZLOWIEK vs AI", "PAPIER KAMIEN", "NOZYCE",
                    f"DO {self.p.wins} WYGRANYCH" if self.p.wins else f"{self.p.rounds} RUNDY"]
        if self.phase == FINAL:
            w = {"robot": "WYGRAL ROBOT", "human": "WYGRYWASZ", "draw": "REMIS"}[self.winner or "draw"]
            return ["CZLOWIEK vs AI", "KONIEC GRY", w, line3]
        if self.phase == ABORT:
            return ["CZLOWIEK vs AI", "KONIEC GRY", "", line3]
        mid = f"ROBOT: {MOVE_ASCII[self.robot]}" if self.phase == REVEAL and self.robot else "..."
        rnd = ("ROZGRZEWKA" if self.warmup else f"RUNDA {self.round_no}" if self.p.wins
               else f"RUNDA {self.round_no}/{self.p.rounds}")
        return ["CZLOWIEK vs AI", rnd, mid, line3]
