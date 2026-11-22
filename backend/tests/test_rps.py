"""Papier, kamień, nożyce: rozpoznanie gestu z 21 punktów i pełny przebieg gry (bez kamery)."""
import json
import random

import numpy as np
import pytest

from app.logic.rps import (FINAL, MOVES, PAPER, ROCK, SCISSORS, WINNING_MOVE, GestureVote, Round, RpsGame, RpsParams,
                           classify, classify3d, classify_hand, is_thumbs_up, is_thumbs_up3d, result)
from app.protocol import waveshare
from app.sim import hand as handmodel

POSE_OF = {ROCK: "fist", PAPER: "open", SCISSORS: "scissors"}


def landmarks(pose: str, dist: float = 0.6, tilt: float = 0.15, side: float = 0.0) -> list:
    """Dłoń z modelu symulacji rzutowana na obraz 640x360 (jak wynik sieci: punkty 0..1)."""
    lm3 = handmodel.landmarks(np.array([dist, side, 0.0]), np.array([-1.0, 0.0, 0.0]), pose, tilt=tilt)
    c = np.stack([-lm3[:, 1], -lm3[:, 2], lm3[:, 0]], 1)
    f, W, H = 466.0, 640.0, 360.0
    u = (W / 2 + f * c[:, 0] / c[:, 2]) / W
    v = (H / 2 + f * c[:, 1] / c[:, 2]) / H
    return [[float(a), float(b)] for a, b in zip(u, v)]


@pytest.mark.parametrize("dist", [0.4, 0.7, 1.0])
@pytest.mark.parametrize("tilt", [0.0, 0.3])
def test_classify_poses(dist, tilt):
    for move, pose in POSE_OF.items():
        assert classify(landmarks(pose, dist, tilt)) == move
    assert classify(landmarks("thumb", dist, tilt)) == ROCK          # kciuk w górę to też zaciśnięte palce
    assert is_thumbs_up(landmarks("thumb", dist, tilt))
    for pose in ("open", "fist", "scissors"):
        assert not is_thumbs_up(landmarks(pose, dist, tilt))


def test_relaxed_hand_still_recognised_but_ambiguous_is_not():
    from app.logic.rps import decide
    assert decide([1, 1, 1, 1]) == PAPER and decide([0, 0, 0, 0]) == ROCK and decide([1, 1, 0, 0]) == SCISSORS
    assert decide([1, 0.45, 1, 0.9]) == PAPER                         # jeden palec zgięty w połowie
    assert decide([0.95, 1, 0.45, 0.1]) == SCISSORS                   # serdeczny niedogięty
    assert decide([0.2, 0.1, 0.35, 0]) == ROCK                        # luźna pięść
    assert decide([0.5, 0.5, 0.5, 0.5]) is None                        # wszystko w połowie: nie wiadomo
    assert decide([1, 1, 0.6, 0.6]) is None                            # nożyce czy papier? lepiej zapytać jeszcze raz


def test_flickering_frames_still_lock_the_gesture():
    """Co trzecia klatka bez gestu (rozmazana dłoń): głosowanie i tak zatwierdza papier."""
    g = RpsGame(RpsParams(rounds=1, cheat_last=False, warmup=False), rng=random.Random(6))
    g.start(0.0)
    p = Player(g, [PAPER])
    p.t = 0.0
    p.run(g.p.intro_s + 3 * g.p.beat_s + 0.02, lambda: "fist")
    k = [0]

    def flicker():
        k[0] += 1
        return None if k[0] % 3 == 0 else "open"
    p.run(1.0, flicker)
    assert g.phase == "reveal" and g.human == PAPER
    assert any("lock" in d for d in g.debug) and any(d.get("ext") for d in g.debug)


def test_classify_rejects_garbage():
    assert classify(None) is None
    assert classify([[0.5, 0.5]] * 21) is None                        # wszystkie punkty w jednym miejscu
    assert classify([[0.1, 0.2]] * 5) is None


def test_rules():
    assert result(ROCK, SCISSORS) == "human" and result(SCISSORS, ROCK) == "robot"
    assert result(PAPER, ROCK) == "human" and result(PAPER, SCISSORS) == "robot"
    assert result(ROCK, ROCK) == "draw"
    for m in MOVES:
        assert result(m, WINNING_MOVE[m]) == "robot"


# --------------------------------------------------------------------------- przebieg gry
class Player:
    """Odwiedzający przed kamerą: 15 klatek/s, poza dłoni zależna od fazy gry."""

    def __init__(self, game: RpsGame, moves: list[str]):
        self.g, self.moves, self.t = game, list(moves), 1000.0
        self.frames = 0

    def run(self, seconds: float, pose_fn, fps: float = 15.0):
        end = self.t + seconds
        while self.t < end:
            self.t += 1.0 / fps
            self.frames += 1
            pose = pose_fn()
            hand = None if pose is None else {"t": self.t, "landmarks": landmarks(pose)}
            self.g.update(self.t, hand)

    def play(self):
        g = self.g
        if g.phase == "countdown":
            return "fist"
        if g.phase in ("read", "reveal"):
            n = len(g.history) - (1 if g.phase == "reveal" else 0)
            return POSE_OF[self.moves[n]]
        return "open"


def test_full_match_thumb_start_fair_rounds_then_cheat():
    g = RpsGame(RpsParams(rounds=3, wheels=True), rng=random.Random(4))
    p = Player(g, [ROCK, PAPER, SCISSORS])
    p.run(0.3, lambda: "open")
    assert not g.active
    p.run(0.9, lambda: "thumb")                                       # kciuk trzymany > 0,5 s
    assert g.phase == "intro"
    oled = g.oled()
    assert oled[0] == "CZLOWIEK vs AI" and len(oled) == 4

    seen_words, wheels = set(), []
    for _ in range(int(40 * 15)):
        p.run(1 / 15, p.play)
        v = g.view(p.t)
        if v["word"]:
            seen_words.add(v["word"])
        w = g.wheels(p.t)
        if w and g.phase == "countdown":
            wheels.append(w)
        if g.phase == FINAL:
            break
    assert g.phase == FINAL
    assert seen_words == {"Papier", "Kamień", "Nożyce", "Teraz!"}
    assert len(g.history) == 3
    assert [r.human for r in g.history] == [ROCK, PAPER, SCISSORS]
    assert [r.cheat for r in g.history] == [False, False, True]
    last = g.history[-1]
    assert last.robot == WINNING_MOVE[SCISSORS] and last.result == "robot"
    assert 0.0 <= last.rec_s <= 0.2                                   # 2 klatki przy 15 kl./s
    v = g.view(p.t)
    assert v["cheat"] == {"human": SCISSORS, "robot": ROCK, "rec_s": last.rec_s, "since_s": last.since_s}
    assert 0.0 <= last.since_s <= 0.5
    assert v["winner"] in ("human", "robot", "draw") and v["stats"]["games"] == 1
    assert any(abs(l) > 0 for l, _ in wheels)                         # koła "pompują" w rytm
    json.dumps(v)                                                     # musi przejść przez WebSocket

    # koniec gry -> wyłączenie, chwila przerwy, kciuk znów uruchamia
    p.run(g.p.final_s + 0.2, lambda: None)
    assert not g.active and g.oled() is None
    p.run(0.9, lambda: "thumb")
    assert not g.active                                               # przerwa po grze
    p.run(g.p.cooldown_s, lambda: "thumb")
    assert g.active


def test_final_waits_for_choice_then_menu_or_again():
    def finished(seed):
        g = RpsGame(RpsParams(rounds=1, cheat_last=False), rng=random.Random(seed))
        p = Player(g, [ROCK])
        g.start(p.t)
        while g.phase != FINAL:
            p.run(1 / 15, p.play)
        return g, p

    # gracz został (dłoń widać): po wyniku i powtórce wynik czeka z przyciskami
    g, p = finished(1)
    p.run(g.p.final_s + 6.0, lambda: "open")
    assert g.phase == FINAL
    # "Zagraj jeszcze raz" (POST /api/game/start) -> od razu nowy mecz
    g.start(p.t)
    assert g.phase == "intro" and g.history == []

    # gracz odszedł: po wyniku i powtórce chwila bez dłoni -> koniec gry (ekran startowy)
    g, p = finished(2)
    p.run(g.p.final_s - 1.0, lambda: "open")
    assert g.phase == FINAL
    p.run(1.0 + g.p.choice_idle_s + 0.2, lambda: None)
    assert not g.active

    # nikt nic nie wybiera, a dłoń wciąż widać: najdłużej final_s + choice_s
    g, p = finished(3)
    p.run(g.p.final_s + g.p.choice_s + 0.2, lambda: "open")
    assert not g.active


def test_fair_round_robot_move_chosen_before_seeing_hand():
    g = RpsGame(RpsParams(rounds=3), rng=random.Random(1))
    g.start(0.0)
    g.update(g.p.intro_s + 0.01, None)
    assert g.phase == "countdown"
    pick = g.robot_pick
    assert pick in MOVES                                              # wylosowane już na starcie odliczania
    p = Player(g, [ROCK, ROCK, ROCK])
    p.t = g.p.intro_s + 0.01
    while g.phase != "reveal":
        p.run(1 / 15, p.play)
    assert g.robot == pick


def test_pumping_fist_before_now_does_not_lock_early():
    """Pięść pompowana w odliczaniu nie może zostać zamrożona jako ruch, zanim padnie "Teraz!"."""
    g = RpsGame(RpsParams(rounds=1, cheat_last=False, warmup=False), rng=random.Random(2))
    g.start(0.0)
    p = Player(g, [PAPER])
    p.t = 0.0
    p.run(g.p.intro_s + 3 * g.p.beat_s - 0.05, lambda: "fist")
    assert g.phase == "countdown" and not g.history
    p.run(0.1, lambda: "fist")                                        # "Teraz!" – pięść jeszcze chwilę
    p.run(0.6, lambda: "open")                                        # i papier
    assert g.history and g.history[0].human == PAPER


def test_miss_repeats_round_and_aborts_after_no_hand():
    g = RpsGame(RpsParams(rounds=3), rng=random.Random(3))
    g.start(0.0)
    p = Player(g, [])
    p.t = 0.0
    p.run(g.p.intro_s + 3 * g.p.beat_s + g.p.read_s + 0.1, lambda: None if g.phase == "read" else "open")
    assert g.phase == "miss" and not g.history
    p.run(g.p.miss_s + 0.1, lambda: "open")
    assert g.phase == "countdown"
    p.run(g.p.no_hand_abort_s + 0.5, lambda: None)                    # odwiedzający odszedł
    assert g.phase in ("abort", "off")
    p.run(g.p.abort_s + 0.2, lambda: None)
    assert not g.active


def test_no_gesture_twice_gives_round_to_robot():
    g = RpsGame(RpsParams(rounds=3, warmup=False), rng=random.Random(5))
    g.start(0.0)
    p = Player(g, [])
    p.t = 0.0
    no_gesture = lambda: None if g.phase == "read" else "open"        # na "Teraz!" gracz nic nie pokazuje
    p.run(g.p.intro_s + 3 * g.p.beat_s + g.p.read_s + 0.1, no_gesture)
    assert g.phase == "miss" and not g.history                          # 1. raz: przypomnienie
    p.run(g.p.miss_s + 3 * g.p.beat_s + g.p.read_s + 0.4, no_gesture)    # zapas na przejścia co 1/15 s
    assert g.phase == "reveal"                                          # 2. raz: walkower
    r = g.history[0]
    assert r.human is None and r.result == "robot" and r.robot in MOVES
    v = g.view(p.t)
    assert v["human"] is None and v["result"] == "robot" and v["score"] == {"human": 0, "robot": 1}
    json.dumps(v)
    assert g.oled()[2].startswith("ROBOT:")
    p.run(g.p.reveal_s + 0.1, lambda: "open")
    assert g.phase == "countdown" and g.round_no == 2                  # gra toczy się dalej


def test_warmup_round_shows_how_to_play_without_points():
    g = RpsGame(RpsParams(rounds=3), rng=random.Random(8))
    g.start(0.0)
    p = Player(g, [PAPER, ROCK, SCISSORS, PAPER])
    p.t = 0.0
    p.run(g.p.intro_s + 0.05, lambda: "open")
    v = g.view(p.t)
    assert g.phase == "countdown" and v["warmup"] and v["round"] == 0
    assert g.robot_pick in MOVES                                       # rozgrzewka zawsze uczciwa
    assert g.oled()[1] == "ROZGRZEWKA"
    while g.phase != "reveal":
        p.run(1 / 15, p.play)
    v = g.view(p.t)
    assert v["warmup"] and v["human"] == PAPER and v["result"] in ("human", "robot", "draw")
    assert g.history == [] and v["score"] == {"human": 0, "robot": 0}  # bez punktu
    p.run(g.p.reveal_s + 0.1, lambda: "open")
    v = g.view(p.t)
    assert g.phase == "countdown" and not v["warmup"] and v["round"] == 1


def test_warmup_without_gesture_starts_real_game_after_reminder():
    g = RpsGame(RpsParams(rounds=3), rng=random.Random(9))
    g.start(0.0)
    p = Player(g, [])
    p.t = 0.0
    no_gesture = lambda: None if g.phase == "read" else "open"
    p.run(g.p.intro_s + 3 * g.p.beat_s + g.p.read_s + 0.1, no_gesture)
    assert g.phase == "miss" and g.warmup                              # przypomnienie, rozgrzewka jeszcze raz
    p.run(g.p.miss_s + 3 * g.p.beat_s + g.p.read_s + 0.4, no_gesture)    # zapas na przejścia co 1/15 s
    assert g.phase == "miss" and not g.history                          # drugi raz: bez walkowera
    p.run(g.p.miss_s + 0.1, lambda: "open")
    assert g.phase == "countdown" and not g.warmup and g.round_no == 1


def _play_scripted(g, human_moves):
    """Gra do końca: w każdej rundzie gracz pokazuje kolejny ruch z listy (po skończeniu listy: papier)."""
    p = Player(g, human_moves + [PAPER] * 20)
    p.t = 0.0
    for _ in range(int(120 * 15)):
        p.run(1 / 15, p.play)
        if g.phase == FINAL:
            break
    return p


def test_best_of_five_ends_at_three_wins_and_cheats_only_at_two_all():
    from app.logic.rps import BEATS

    class Rigged(random.Random):
        """Robot losuje z kolejki: tak ustawiamy wynik rund."""
        def __init__(self, picks):
            super().__init__(0)
            self.picks = list(picks)

        def choice(self, seq):
            return self.picks.pop(0) if self.picks else super().choice(seq)

    loses_to = {v: k for k, v in BEATS.items()}                       # ruch, który przegrywa z danym
    # 1) 3:0 dla gracza: trzy uczciwe rundy, bez oszukiwania i bez powtórki
    human = [PAPER, PAPER, PAPER]
    g = RpsGame(RpsParams(rounds=9, wins=3, warmup=False), rng=Rigged([BEATS[m] for m in human]))
    g.start(0.0)
    _play_scripted(g, human)
    assert g.phase == FINAL and g.score() == {"human": 3, "robot": 0} and g.winner == "human"
    assert not any(r.cheat for r in g.history) and g.view(1e9)["cheat"] is None

    # 2) 2:2 -> runda decydująca: robot podgląda i wygrywa 3:2 (wcześniej tylko uczciwie)
    human = [PAPER, PAPER, ROCK, ROCK, SCISSORS]
    robot = [BEATS[PAPER], BEATS[PAPER], loses_to[ROCK], loses_to[ROCK]]   # 2 dla gracza, 2 dla robota
    g = RpsGame(RpsParams(rounds=9, wins=3, warmup=False), rng=Rigged(robot))
    g.start(0.0)
    _play_scripted(g, human)
    assert g.phase == FINAL and g.score() == {"human": 2, "robot": 3}
    assert [r.cheat for r in g.history] == [False, False, False, False, True]
    assert g.history[-1].human == SCISSORS and g.history[-1].robot == ROCK
    assert g.view(1e9)["cheat"]["robot"] == ROCK
    assert g.oled() is not None


def test_best_of_five_draws_do_not_count():
    g = RpsGame(RpsParams(rounds=9, wins=3, warmup=False), rng=random.Random(0))
    g.start(0.0)
    g.history = [Round(PAPER, PAPER, "draw", False)] * 2 + [Round(PAPER, ROCK, "human", False)] * 2
    assert not g.match_over and g.round_no == 5
    g.history.append(Round(ROCK, PAPER, "robot", False))
    assert not g.match_over and not g.decider                         # 2:1, remisy nie liczą się do 3


def test_camera_without_landmarks_disables_game():
    g = RpsGame()
    g.start(0.0)
    g.update(0.1, None, available=False)
    assert not g.active
    v = g.view(0.1, available=False)
    assert v["available"] is False and v["active"] is False


def test_wheels_dance_and_oled_lines_are_ascii():
    g = RpsGame(RpsParams(rounds=1, cheat_last=True, wheel_speed=0.15, warmup=False))
    g.start(0.0)
    p = Player(g, [PAPER])
    p.t = 0.0
    while g.phase != "reveal":
        p.run(1 / 15, p.play)
    assert g.outcome == "robot"
    lw, rw = g.wheels(p.t + 0.01)
    assert lw == -rw and abs(lw) > 0                                  # taniec w miejscu
    for line in g.oled():
        assert line.isascii() and len(line) <= waveshare.OLED_COLS


def test_waveshare_oled_frame():
    raw = waveshare.oled(2, "Robot: nożyce – wygrał!" + "x" * 40)
    obj = json.loads(raw)
    assert obj["T"] == 3 and obj["lineNum"] == 2
    assert obj["Text"].isascii() and len(obj["Text"]) == waveshare.OLED_COLS
    assert obj["Text"].startswith("Robot: nozyce")
    assert waveshare.OLED_DEFAULT == b'{"T":-3}\n'
    assert waveshare.parse_init('{"T":3,"lineNum":0,"Text":"x"};{"T":1,"L":1,"R":1}') == \
        [{"T": 3, "lineNum": 0, "Text": "x"}]                         # komenda ruchu odrzucona


# --------------------------------------------------------------------------- rozpoznanie 3D
def world(pose: str, yaw_deg: float = 0.0, tilt: float = 0.15) -> list:
    """Punkty 3D dłoni [m] w układzie kamery (x w prawo, y w dół), względem nadgarstka – jak "world landmarks"."""
    a = np.radians(yaw_deg)
    toward = np.array([-np.cos(a), np.sin(a), 0.0])
    lm3 = handmodel.landmarks(np.array([0.6, 0.0, 0.0]), toward, pose, tilt=tilt)
    c = np.stack([-lm3[:, 1], -lm3[:, 2], lm3[:, 0]], 1)
    return (c - c[0]).tolist()


@pytest.mark.parametrize("yaw", [0, 30, 60])
def test_classify3d_robust_to_hand_rotation(yaw):
    for move, pose in POSE_OF.items():
        assert classify3d(world(pose, yaw)) == move, (move, yaw)
    assert is_thumbs_up3d(world("thumb", yaw))
    assert not is_thumbs_up3d(world("fist", yaw))


def test_3d_beats_2d_on_strongly_turned_hand():
    """Dłoń obrócona bokiem do kamery: na obrazie palce się skracają, w 3D kąty w stawach zostają."""
    a = np.radians(60)
    lm3 = handmodel.landmarks(np.array([0.6, 0.0, 0.0]), np.array([-np.cos(a), np.sin(a), 0.0]), "open", tilt=0.15)
    c = np.stack([-lm3[:, 1], -lm3[:, 2], lm3[:, 0]], 1)
    hand = {"landmarks": [[0.5 + float(x / z) * 0.6, 0.5 + float(y / z) * 1.1] for x, y, z in c],
            "world": (c - c[0]).tolist()}
    assert classify_hand(hand) == PAPER
    assert classify_hand({"landmarks": hand["landmarks"]}) in (PAPER, None)    # bez 3D – bywa niepewnie


def test_gesture_vote_needs_majority_and_ignores_single_bad_frame():
    v = GestureVote(n=5, need=3)
    seq = [PAPER, PAPER, ROCK, PAPER, PAPER, None, PAPER]
    outs = [v.update(i, g) for i, g in enumerate(seq)]
    assert outs[1] is None and outs[3] == PAPER and all(o == PAPER for o in outs[3:])
    for i in range(10, 14):
        out = v.update(i, ROCK)
    assert out == ROCK
    assert v.update(14, ROCK) == ROCK and v.update(14, PAPER) == ROCK      # ta sama klatka liczy się raz
