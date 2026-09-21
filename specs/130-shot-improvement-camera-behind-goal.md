# 130 — Shot position: camera behind the goal

New "Ammuntapaikka" choice "Kamera maalin takana: sinisestä viivasta maalin
perään (19,6 m)" (`blue_line_goal_back`): shooting from the blue line while
the camera is behind the goal, so the hit is the puck reaching the back of
the net: 18.5 m (blue line to goal line) + 1.12 m (IIHF goal depth at the
base) = 19.62 m. The default stays "Sinisestä viivasta maaliin (18,5 m)".
Sources for the goal depth: IIHF goal spec (net support depth 60 cm at the
top, 112 cm at the bottom) via Dimensions.com / XbotGo goal guides.
Files: `core/claps.py`, `tests/test_claps.py`.
