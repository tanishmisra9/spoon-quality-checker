import json
import subprocess
import sys
from types import SimpleNamespace

import anthropic
import cv2
import numpy as np

from scripts import label


def test_schema_is_structured_output_safe():
    s = anthropic.transform_schema(label.Label)  # raises if the schema uses unsupported constructs
    assert set(s["properties"]) >= {"contains_object", "object_bbox", "faults"}
    assert set(label.FAULTS) == {"blur", "dark", "bright", "contrast", "glare"}  # resolution is never labeled


def test_label_one_clamps_severity_and_uses_pack_prompt(tmp_path):
    img = tmp_path / "a.jpg"
    cv2.imwrite(str(img), np.full((64, 64, 3), 128, np.uint8))
    faults = {f: dict(present=False, severity=0, where="none") for f in label.FAULTS}
    faults["blur"] = dict(present=True, severity=9, where="object")  # out of range on purpose
    fake = label.Label(contains_object=True, metallic=True, object_bbox=[0.1, 0.1, 0.9, 0.9], view="top",
                       faults=faults, retake=True)
    seen = {}

    def parse(**kw):
        seen.update(kw)
        return SimpleNamespace(parsed_output=fake, usage=SimpleNamespace(input_tokens=10, output_tokens=5), stop_reason="end_turn")

    client = SimpleNamespace(messages=SimpleNamespace(parse=parse))
    d, tin, tout = label.label_one(client, "claude-haiku-5-5", img)
    assert d["faults"]["blur"]["severity"] == 3 and (tin, tout) == (10, 5)
    assert "metal spoon" in seen["messages"][0]["content"][1]["text"]
    assert seen["output_config"] == {"effort": "low"}


def test_pack_swap_changes_schema_without_code_edits():
    code = "from scripts import label; print(label.OBJECT, [v for v in label.Label.model_fields['view'].annotation.__args__])"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**__import__('os').environ, "PACK": "fork"})
    assert "metal fork" in out.stdout and "tines_close" in out.stdout, out.stderr
