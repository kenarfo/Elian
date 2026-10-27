# ==========================
# ELIAN AI ASSISTANT
# Version 0.6
# SkillsLoader.py
#
# LEGACY (Phase 1): skills remain as a compatibility fast route.
# Prefer core.ToolRegistry adapters for new capabilities.
# ==========================

import importlib
import os


skills = []

SKILLS_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "Skills"
)


def load_skills():

    global skills

    skills = []


    if not os.path.exists(SKILLS_FOLDER):

        return



    for file in sorted(
        os.listdir(SKILLS_FOLDER)
    ):

        if (
            file.endswith(".py")
            and file != "__init__.py"
        ):

            name = file[:-3]


            try:

                module = importlib.import_module(
                    f"Skills.{name}"
                )


                if hasattr(
                    module,
                    "run"
                ):

                    skills.append(module)


                    print(
                        f"Załadowano skill: {name}"
                    )


            except Exception as e:

                print(
                    f"Błąd ładowania {name}:",
                    e
                )



def run_skills(text):

    for skill in skills:

        try:

            if hasattr(skill, "match") and not skill.match(text):

                continue

            result = skill.run(text)

            if result:

                return result

        except Exception as e:

            print(
                "Błąd skill:",
                e
            )

    return None
