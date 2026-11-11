/**
 * 医疗域 · 静态配置常量
 *
 * Mock 数据已全部迁移至 API（api/client.ts）。
 * 本文件仅保留仍被前端组件引用的静态配置：分类列表、tone 映射、机构元数据。
 *
 * 如需还原演示数据，请通过后端 seed 接口写入数据库。
 */

import type { Tone } from '../lib/tone'
import type { DrugCategory, GuidelineCategory } from '../types/view'

/* ════════════════════════ 指南知识库 ════════════════════════ */

/** 发布机构元数据（配色走主题语义色 token 名，由页面解析为具体 token） */
export const GUIDELINE_ORG_META: Record<GuidelineCategory, { org: string; full: string; tone: Tone }> = {
  NCCN指南: { org: 'NCCN', full: '美国国立综合癌症网络', tone: 'accent' },
  CSCO指南: { org: 'CSCO', full: '中国临床肿瘤学会', tone: 'danger' },
  ESMO指南: { org: 'ESMO', full: '欧洲肿瘤内科学会', tone: 'success' },
}

export const GUIDELINE_CATEGORIES: GuidelineCategory[] = ['NCCN指南', 'CSCO指南', 'ESMO指南']

/* ════════════════════════ 文献知识库 ════════════════════════ */

export const LITERATURE_CATEGORIES = ['全部', '肺癌', '乳腺癌', '消化道肿瘤', '肝癌', '头颈肿瘤']

/* ════════════════════════ 药品知识库 ════════════════════════ */

/** 药品分类 → 语义色 tone（由页面解析为具体 token） */
export const DRUG_CATEGORY_TONE: Record<DrugCategory, Tone> = {
  化疗药物: 'purple',
  靶向药物: 'accent',
  免疫治疗: 'success',
  内分泌治疗: 'danger',
  骨改良药物: 'warning',
}

export const DRUG_CATEGORIES: DrugCategory[] = ['化疗药物', '靶向药物', '免疫治疗', '内分泌治疗', '骨改良药物']
