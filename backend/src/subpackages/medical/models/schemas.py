"""医疗域 Pydantic Schema — 请求/响应模型基类。

- VmBase: 所有 camelCase 视图模型基类
- CreateConsultationRequest: 创建咨询登记请求
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class VmBase(BaseModel):
    """所有医疗域响应模型的基类：内部 snake_case，序列化输出 camelCase。"""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        protected_namespaces=(),
    )


class CreateConsultationRequest(VmBase):
    """创建咨询登记请求。前端先调核心建会话，拿到 session_id 后再调此接口登记。"""
    session_id: str = Field(..., description="核心返回的会话 ID")
    patient_id: str = Field(..., description="患者 ID")
    title: str = Field("", description="咨询标题")
