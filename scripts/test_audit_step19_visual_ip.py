"""
Audit 2026-07-20 #19 — visual-IP safety note in design prompts.

The text trademark screen catches branded NAMES; an AI image can still be
derivative (character silhouette, logo, art style). The design prompts now
instruct the generator toward entirely original artwork.

Usage: python scripts/test_audit_step19_visual_ip.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []


def check(name, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        failures.append(name)


from app.agents.image.pod_design_agent import PODDesignAgent

agent = PODDesignAgent.__new__(PODDesignAgent)
prompt = agent._build_design_prompt("Cozy Cat Poster", "a sleepy tabby cat, warm tones", "digital_download")
low = prompt.lower()
check("design prompt forbids recognizable characters", "recognizable characters" in low)
check("design prompt forbids brand logos/trademarks", "logo" in low and "trademark" in low)
check("design prompt asks for original", "original" in low)

# PDF page prompt
from app.services.pdf_generation_service import PDFGenerationService
svc = PDFGenerationService.__new__(PDFGenerationService)
pdf_prompt = svc._build_page_prompt("Weekly Planner", "clean minimal", "weekly grid", 2, 10).lower()
check("pdf page prompt has IP safety note", "original" in pdf_prompt and "copyrighted" in pdf_prompt)

print()
if failures:
    print(f"{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("All audit-#19 visual-IP tests passed.")
