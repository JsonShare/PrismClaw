"""result-interpreter: Plain Language结果解释器

将统计结果转化为医生能理解的通俗语言。
核心原则：效应量优先于P值，禁止误导性词汇。
"""

__version__ = "1.0.0"
__author__ = "medical-skills-build team"

from .interpreter_factory import InterpreterFactory
from .effect_size_interpreter import EffectSizeInterpreter
from .conclusion_writer import ConclusionWriter
from .template_engine import TemplateEngine

__all__ = [
    "InterpreterFactory",
    "EffectSizeInterpreter",
    "ConclusionWriter",
    "TemplateEngine",
]