# 132 — Ammuntapaikka: hyökkäysalueen aloituspisteiden väli

Uusi valinta "Hyökkäysalueen aloituspisteiden välistä maaliin (6 m)"
(`attack_dots`): ammutaan hyökkäysalueen kahden aloituspisteen välisestä
linjasta maaliin. Aloituspisteet ovat 6 m päässä maaliviivasta (IIHF), joten
matka on 6 m. Huom. lyhyt matka: laukauksen ja osuman väli on vain
~0,1-0,3 s, jolloin ikkuna (`hit_delay_window`) on 0,16-0,42 s; tunnistus
voi olla tällä matkalla epätarkempi. Tiedostot: `core/claps.py`,
`tests/test_claps.py`.
