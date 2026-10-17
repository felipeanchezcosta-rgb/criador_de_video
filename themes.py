import random
import re

VISUAL_STYLE = (
    "dark cinematic digital illustration, moody lighting with a clearly lit focal point, readable shadows, "
    "teal and crimson neon accents, volumetric haze, high contrast, "
    "no text, no letters, no logos, no watermark"
)

STORIES = [
    {
        "key": "stuxnet",
        "title_en": "The Stuxnet Worm: The Cyberweapon That Sabotaged Iran's Nuclear Program",
        "title_pt": "O verme Stuxnet: a arma cibernética que sabotou o programa nuclear do Irã",
        "angle": "A mysterious piece of code quietly destroys machines it was never supposed to touch.",
        "case": "Stuxnet and the sabotage of centrifuges at Iran's Natanz enrichment plant",
    },
    {
        "key": "mitnick",
        "title_en": "Kevin Mitnick: The Most Wanted Hacker in America",
        "title_pt": "Kevin Mitnick: o hacker mais procurado dos EUA",
        "angle": "A social engineering master and the FBI manhunt that followed him.",
        "case": "Kevin Mitnick's social engineering career, FBI manhunt and 1995 arrest",
    },
    {
        "key": "twitter2020",
        "title_en": "The 2020 Twitter Hack: How a Teenager Took Over Famous Accounts",
        "title_pt": "O hack do Twitter em 2020: como um adolescente invadiu contas famosas",
        "angle": "One phone call, one internal tool, and the biggest accounts on the platform.",
        "case": "The July 2020 Twitter Bitcoin scam that hijacked high-profile accounts",
    },
    {
        "key": "mtgox",
        "title_en": "Mt. Gox: The Bitcoin Exchange That Lost Hundreds of Thousands of Coins",
        "title_pt": "Mt. Gox: a corretora de Bitcoin que perdeu centenas de milhares de moedas",
        "angle": "The largest Bitcoin exchange on Earth vanishes overnight.",
        "case": "The collapse of the Mt. Gox exchange in 2014",
    },
    {
        "key": "equifax",
        "title_en": "The Equifax Breach: How 147 Million Americans Were Exposed",
        "title_pt": "O vazamento da Equifax: como 147 milhões de americanos foram expostos",
        "angle": "One unpatched server and the personal data of half a nation.",
        "case": "The 2017 Equifax data breach",
    },
    {
        "key": "bangladesh",
        "title_en": "The Bangladesh Bank Heist: The Hackers Who Nearly Stole $1 Billion",
        "title_pt": "O assalto ao Banco de Bangladesh: os hackers que quase roubaram US$ 1 bilhão",
        "angle": "A single typo stops one of the biggest bank robberies in history.",
        "case": "The 2016 Bangladesh Bank SWIFT heist",
    },
    {
        "key": "morris",
        "title_en": "The Morris Worm: The First Internet Worm That Broke the Web in 1988",
        "title_pt": "O verme Morris: o primeiro verme da internet que derrubou a rede em 1988",
        "angle": "A graduate student's experiment spirals out of control.",
        "case": "The Morris Worm of 1988",
    },
    {
        "key": "sony2014",
        "title_en": "The Sony Pictures Hack: The Cyberattack That Shocked Hollywood",
        "title_pt": "O hack da Sony Pictures: o ciberataque que chocou Hollywood",
        "angle": "A studio's secrets spill onto the internet.",
        "case": "The 2014 Sony Pictures Entertainment hack",
    },
    {
        "key": "target2013",
        "title_en": "The Target Breach: How Hackers Got In Through an Air Conditioning Vendor",
        "title_pt": "O vazamento da Target: como hackers entraram por um fornecedor de ar-condicionado",
        "angle": "The weakest link was not Target at all.",
        "case": "The 2013 Target breach through an HVAC vendor",
    },
    {
        "key": "ftx",
        "title_en": "The Collapse of FTX: How a Crypto Empire Vanished in Days",
        "title_pt": "A queda da FTX: como um império cripto sumiu em dias",
        "angle": "A billion-dollar empire unravels in a single week.",
        "case": "The November 2022 collapse of the FTX exchange",
    },
]

CUSTOM_OPTION = len(STORIES) + 1


def _to_theme(story):
    return {
        "key": story["key"],
        "name": story["title_en"],
        "title_pt": story["title_pt"],
        "angle": story["angle"],
        "cases": [story["case"]],
    }


def _custom_theme(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "custom"
    return {
        "key": f"custom-{slug}",
        "name": text.strip(),
        "title_pt": "",
        "angle": "A real, well documented technology story told like a thriller.",
        "cases": [text.strip()],
    }


def list_themes():
    return [(s["key"], s["title_en"]) for s in STORIES]


def get_theme(key=None):
    if not key:
        return _to_theme(random.choice(STORIES))
    key = str(key).lower().strip()
    if key.isdigit():
        number = int(key)
        if 1 <= number <= len(STORIES):
            return _to_theme(STORIES[number - 1])
    for story in STORIES:
        if story["key"] == key:
            return _to_theme(story)
    valid = ", ".join(s["key"] for s in STORIES)
    raise ValueError(f"Tema inválido '{key}'. Opções: {valid}")


def print_menu():
    print()
    for number, story in enumerate(STORIES, start=1):
        print(f"{number:>2}. {story['title_en']}")
        print(f"    {story['title_pt']}")
        print()
    print(f"{CUSTOM_OPTION:>2}. Digitar meu próprio tema")
    print()


def choose_theme_interactive():
    print_menu()
    while True:
        answer = input(f"Escolha de 1 a {CUSTOM_OPTION}: ").strip()
        if answer.isdigit():
            number = int(answer)
            if 1 <= number <= len(STORIES):
                return _to_theme(STORIES[number - 1])
            if number == CUSTOM_OPTION:
                text = input("Digite o tema (de preferência em inglês): ").strip()
                if text:
                    return _custom_theme(text)
        print("Opção inválida, tente de novo.")
