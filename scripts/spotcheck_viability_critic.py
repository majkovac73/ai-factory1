"""
LIVE (non-mocked) spot-check of ProductViabilityCriticAgent judgment.
Runs the real LLM against a mix of concepts we expect to PASS and FAIL,
so a human can confirm the critic's judgment matches their own read.
Small cost (gpt-4o-mini). Usage: python scripts/spotcheck_viability_critic.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.product_viability_critic import ProductViabilityCriticAgent

CONCEPTS = [
    # expect PASS
    ("PASS?", {
        "product_name": "Plant Parent Weekly Care Planner",
        "product_format": "pdf_planner_or_guide",
        "description": "A 5-page printable planner to track watering, fertilizing, "
                       "light rotation and repotting dates for houseplant collections.",
        "target_audience": "houseplant enthusiasts who kill plants by forgetting to water",
    }),
    ("PASS?", {
        "product_name": "Cozy Cottagecore Mushroom Village Coloring Page",
        "product_format": "coloring_page",
        "description": "A whimsical hand-drawn scene of tiny mushroom cottages with "
                       "snails, ferns and fairy lights, dense with detail for relaxing coloring.",
        "target_audience": "adults who color to unwind, cottagecore fans",
    }),
    ("PASS?", {
        "product_name": "Retro 70s Sunset Gradient Phone Wallpaper",
        "product_format": "phone_wallpaper",
        "description": "A warm retro sunset with layered orange-to-purple gradient stripes "
                       "and a grainy film texture, sized for modern phones.",
        "target_audience": "people who like nostalgic 70s aesthetic phone backgrounds",
    }),
    # expect FAIL
    ("FAIL?", {
        "product_name": "Motivation",
        "product_format": "single_print",
        "description": "A poster about motivation.",
        "target_audience": "everyone",
    }),
    ("FAIL?", {
        "product_name": "Gardening Tips Coloring Page",
        "product_format": "coloring_page",
        "description": "A coloring page that teaches gardening tips and explains composting.",
        "target_audience": "people who want to learn gardening",
    }),
    ("FAIL?", {
        "product_name": "Live Laugh Love Quote Print",
        "product_format": "single_print",
        "description": "The words 'Live Laugh Love' in a plain font on a white background.",
        "target_audience": "anyone who likes quotes",
    }),
]


def main():
    critic = ProductViabilityCriticAgent()
    for expectation, concept in CONCEPTS:
        res = critic.critique(concept)
        verdict = "PASS" if res["passed"] else "FAIL"
        print(f"\nexpected {expectation:6} -> got {verdict} (score={res['score']})")
        print(f"  {concept['product_name']}")
        print(f"  reason: {res['reason']}")


if __name__ == "__main__":
    main()
