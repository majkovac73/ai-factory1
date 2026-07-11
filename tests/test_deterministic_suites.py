"""
STEP 103 D-5 — a pytest gate over the deterministic script-suites.

The project's tests are ad-hoc scripts in scripts/ (each exits non-zero on
failure). Rather than rewrite 40+ of them, this runs the KNOWN-deterministic,
offline (fully-mocked) suites as subprocesses and asserts each exits 0, so a
single `pytest` (in CI) verifies the whole thing before deploy.

Excluded on purpose: suites that make real network calls (Google Trends:
test_step88_autonomy, test_step90_product_gate) or real LLM/image calls
(spotcheck_*, the live critic check) — those aren't deterministic in CI.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# Curated deterministic, offline suites (fully mocked; isolate their own temp DB).
DETERMINISTIC = [
    "test_sanitizer.py",
    "test_seo_schema.py",
    "test_state_machine.py",
    # (test_openrouter_image_provider.py excluded — it makes a REAL image API call)
    "test_step89_pipeline_orchestrator.py",
    "test_step91_pdf_and_formats.py",
    "test_step92_file_and_publish_readback.py",
    "test_step93_taxonomy_readback.py",
    "test_step94_file_content_type.py",
    "test_step95_when_made.py",
    "test_step96_content_quality.py",
    "test_step100b_consistency_remake.py",
    "test_step100d_coloring_page_prompts.py",
    "test_step100f_pdf_planner_prompts.py",
    "test_step100g_delivery_mockups.py",
    "test_step100i_tumblr_on_listing.py",
    "test_step100j_white_background.py",
    "test_step100k_pdf_mockups.py",
    "test_step100l_pdf_page_qa.py",
    "test_step84_performance.py",
    # step 102
    "test_step102_viability_critic.py",
    "test_step102_api_auth.py",
    "test_step102_pinterest_guard.py",
    "test_step102_oauth_refresh_lock.py",
    "test_step102_receipt_revenue_retry.py",
    "test_step102_pricing.py",
    "test_step102_spend_and_metadata.py",
    "test_step102_pod_margin.py",
    "test_step102_pipeline_resume.py",
    "test_step102_pdf_qa_size.py",
    "test_step102_aspect_pagecount.py",
    "test_step102_p2_fixes.py",
    "test_step102_shipping_profile.py",
    "test_step102_selfheal_scenecache.py",
    "test_step102_pod_mockups.py",
    # step 103
    "test_step103_trademark.py",
    "test_step103_compliance.py",
    "test_step103_backup.py",
    "test_step103_seo.py",
    "test_step103_dedup.py",
    "test_step103_market.py",
    "test_step103_learning_loop.py",
    "test_step103_bundle_cleanup.py",
    "test_step103_planner_render.py",
    "test_step103_seasonality.py",
    "test_step103_sections_boards.py",
    "test_step103_fees_prune.py",
    "test_step103_tierd.py",
    "test_step103_text_overlay.py",
    "test_step103_seamless.py",
    "test_step103_d2b_d6.py",
    "test_step104_seasonality.py",
    "test_step104_seasonal_lifecycle.py",
    "test_step104_learning_signals.py",
    "test_step104_trend_direction_stale.py",
]


@pytest.mark.parametrize("script", DETERMINISTIC)
def test_script_suite(script):
    path = SCRIPTS / script
    if not path.exists():
        pytest.skip(f"{script} not found")
    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, f"{script} failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-2000:]}"
