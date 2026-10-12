"""System prompts live only in config/prompts/, and each one fits the code that fills it."""

import ast
import shutil
import tempfile
import unittest
from pathlib import Path

import src.prompts as prompts

ROOT = Path(__file__).resolve().parents[1]

def inline_prompts(source: str, label: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name) and "SYSTEM" in target.id.upper():
                    found.append(f"{label}: {target.id} is a system prompt written in code")
        if (isinstance(node, ast.Tuple) and len(node.elts) == 2 and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == "system"):
            found.append(f"{label}: a ('system', …) message is built in code")
        if (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "SystemMessage"
                and any(isinstance(k.value, ast.Constant) for k in node.keywords)):
            found.append(f"{label}: SystemMessage with literal text")
    return found


class PromptFilesTest(unittest.TestCase):
    def test_every_declared_prompt_has_a_file_and_back(self):
        files = {p.stem for p in prompts.PROMPT_DIR.glob("*.md")}
        self.assertEqual(files, set(prompts.PROMPTS),
                         "declared in src/prompts.py or present in config/prompts/, not both")

    def test_every_prompt_renders_with_exactly_its_placeholders(self):
        for name, needed in prompts.PROMPTS.items():
            with self.subTest(prompt=name):
                text = prompts.system_prompt(name, **{key: f"<{key}>" for key in needed})
                self.assertTrue(text.strip())
                # A placeholder is {{name}}. A bare "{{ }}" is prompt text: the
                # specialist rules tell the model never to leave one in its answer.
                self.assertEqual(prompts._PLACEHOLDER.findall(text), [],
                                 "a placeholder survived rendering")

    def test_no_system_prompt_is_written_in_code(self):
        problems = []
        for path in sorted((ROOT / "src").rglob("*.py")):
            label = path.relative_to(ROOT).as_posix()
            if "__pycache__" in path.parts:
                continue
            problems += inline_prompts(path.read_text(encoding="utf-8"), label)
        self.assertEqual(problems, [])

    def test_the_scan_catches_a_prompt_written_back_into_code(self):
        self.assertTrue(inline_prompts('SYSTEM_PROMPT = "x"\nm = [("system", "y")]', "probe"))

    def test_a_file_asking_for_an_unfilled_value_is_refused(self):
        probe = "document_classification"
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / f"{probe}.md"
            shutil.copy(prompts.PROMPT_DIR / f"{probe}.md", broken)
            broken.write_text(broken.read_text(encoding="utf-8") + "\n{{industry}}\n", encoding="utf-8")
            original, prompts.PROMPT_DIR = prompts.PROMPT_DIR, Path(tmp)
            prompts.prompt_text.cache_clear()
            try:
                with self.assertRaises(prompts.PromptError):
                    prompts.prompt_text(probe)
            finally:
                prompts.PROMPT_DIR = original
                prompts.prompt_text.cache_clear()


if __name__ == "__main__":
    unittest.main()
