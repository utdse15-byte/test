# Wave 4 focused verification commands

All commands were run from the repository root with `PYTHONPATH=src` where needed.
No command contacted a provider, read credentials, or made a paid request.

```bash
python -m pytest -q tests/test_product_polish_finishing_journey.py
python -m pytest -q tests/test_gui_page_shell_scripts.py
python -m pytest -q tests/test_gui_edit.py
python -m pytest -q tests/test_native_cut.py

python -m pytest -q tests/test_edit_v3.py \
  -k 'test_edit_page_neither_tier_available_keeps_original_honest_empty_state or test_edit_page_defaults_to_tier2_when_only_timeline_exists or test_edit_page_defaults_to_tier1_with_toggle_when_final_exists or test_edit_js_carries_tier2_pool_sequencing_structure or test_edit_page_renders_caption_style_panel_with_hints'

python -m pytest -q tests/test_export_center.py
python -m pytest -q tests/test_gui_finish.py \
  -k 'not card_preview and not frame_and_strip and not packaging_cover_frame and not packaging_teaser_final'
python -m pytest -q \
  tests/test_gui_finish.py::test_packaging_teaser_final_length_warning

python -m pytest -q \
  tests/test_product_polish_finishing.py \
  tests/test_roundtrip_baseline_absent.py \
  tests/test_roundtrip_needs_baseline.py \
  tests/test_roundtrip_fcpxml.py \
  tests/test_exports_table_alignment.py \
  tests/test_windows_export_conform.py

python -m pytest -q \
  tests/test_gui_modes.py \
  tests/test_fp_state_perf.py::test_render_edit_zero_explain_zero_spawns

python -m compileall -q src tests
node --check <rendered common/pages/pages-t/edit/exports JavaScript>
git diff --check
```

The four remaining GUI media/FFmpeg paths were not claimed as passed in this
sandbox. The complete `tests/test_edit_v3.py` suite was also not claimed as
passed; only the five directly affected first-paint/copy paths above were run.
