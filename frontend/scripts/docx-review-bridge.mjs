import fs from 'node:fs/promises'
import {
  DocxReviewer,
  createReviewerBridge,
  executeToolCall,
} from '@eigenpal/docx-editor-agents'

function parseArgs(argv) {
  const args = {}
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i]
    if (!token.startsWith('--')) continue
    const key = token.slice(2)
    const value = argv[i + 1]
    if (value && !value.startsWith('--')) {
      args[key] = value
      i += 1
    } else {
      args[key] = 'true'
    }
  }
  return args
}

function fail(message, code = 'DOCX_REVIEW_BRIDGE_ERROR') {
  process.stdout.write(JSON.stringify({ ok: false, code, error: message }))
  process.exit(1)
}

function parseReadDocumentText(contentText) {
  const lines = String(contentText ?? '').split(/\r?\n/)
  const ordered = []
  const byParaId = new Map()
  for (const line of lines) {
    const match = /^\[([^\]]+)\]\s?(.*)$/.exec(line)
    if (!match) continue
    const paraId = match[1] ?? ''
    const text = match[2] ?? ''
    const entry = { paraId, text }
    ordered.push(entry)
    byParaId.set(paraId, entry)
  }
  return { ordered, byParaId, text: String(contentText ?? '') }
}

async function readDocumentSnapshot(reviewer) {
  const bridge = createReviewerBridge(reviewer)
  const result = executeToolCall('read_document', {}, bridge)
  if (!result?.success) {
    throw new Error(String(result?.error || 'read_document failed'))
  }
  return {
    bridge,
    ...parseReadDocumentText(String(result.data ?? '')),
  }
}

function resolveParaId(operation, byParaId) {
  const ref = String(operation?.ref ?? '').trim()
  if (!ref) {
    throw new Error('Missing DOCX paraId ref.')
  }
  if (!byParaId.has(ref)) {
    throw new Error(`Unknown DOCX paraId: ${ref}`)
  }
  return ref
}

function validateOperations(operations) {
  if (!Array.isArray(operations) || operations.length === 0) {
    throw new Error('operations must be a non-empty array')
  }
  const seenRefs = new Set()
  for (const operation of operations) {
    if (!operation || typeof operation !== 'object') {
      throw new Error('every operation must be an object')
    }
    const op = String(operation.op ?? '')
    const ref = String(operation.ref ?? '')
    const supported = new Set(['suggest_change'])
    if (!supported.has(op)) {
      throw new Error(`Unsupported docx op: ${op}. Supported op is suggest_change.`)
    }
    if (seenRefs.has(ref)) {
      throw new Error(`Duplicate docx ref in one batch is not allowed: ${ref}`)
    }
    seenRefs.add(ref)
    if (!ref) {
      throw new Error('DOCX suggest_change requires a paraId ref')
    }
    if (typeof operation.search !== 'string') {
      throw new Error(`DOCX suggest_change requires string search for ${ref}`)
    }
    if (typeof operation.replaceWith !== 'string') {
      throw new Error(`DOCX suggest_change requires string replaceWith for ${ref}`)
    }
  }
}

function summarizeOperations(operations) {
  let insertions = 0
  let deletions = 0
  const noteParts = []

  for (const operation of operations) {
    const op = String(operation.op ?? '')
    const ref = String(operation.ref ?? '')
    const before = typeof operation.search === 'string' ? operation.search : ''
    const after = typeof operation.replaceWith === 'string' ? operation.replaceWith : ''
    insertions += after.length
    deletions += before.length
    noteParts.push(`${op} ${ref}`)
  }

  return {
    insertions,
    deletions,
    note: noteParts.join('; '),
  }
}

async function readMode(inputPath) {
  if (!inputPath) fail('Missing --input')
  const buffer = await fs.readFile(inputPath)
  const arrayBuffer = buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength)
  const reviewer = await DocxReviewer.fromBuffer(arrayBuffer, 'PrismClaw Agent')
  const { text } = await readDocumentSnapshot(reviewer)
  process.stdout.write(JSON.stringify({ ok: true, text }))
}

async function buildMode(inputPath, operationsPath, outputPath, author) {
  if (!inputPath) fail('Missing --input')
  if (!operationsPath) fail('Missing --operations')
  if (!outputPath) fail('Missing --output')

  const rawInput = await fs.readFile(inputPath)
  const inputBuffer = rawInput.buffer.slice(rawInput.byteOffset, rawInput.byteOffset + rawInput.byteLength)
  const operations = JSON.parse(await fs.readFile(operationsPath, 'utf-8'))

  validateOperations(operations)

  const reviewer = await DocxReviewer.fromBuffer(inputBuffer, author || 'PrismClaw Agent')
  const { bridge, byParaId } = await readDocumentSnapshot(reviewer)

  for (const operation of operations) {
    const paraId = resolveParaId(operation, byParaId)
    const search = String(operation.search)
    const replaceWith = String(operation.replaceWith)

    const toolResult = executeToolCall(
      'suggest_change',
      { paraId, search, replaceWith },
      bridge,
    )
    if (!toolResult?.success) {
      throw new Error(String(toolResult?.error || `suggest_change failed for ${paraId}`))
    }
  }

  const outputBuffer = await reviewer.toBuffer()
  await fs.writeFile(outputPath, Buffer.from(outputBuffer))

  const summary = summarizeOperations(operations)
  const pendingChanges = reviewer.getChanges().length
  process.stdout.write(
    JSON.stringify({
      ok: true,
      pending_changes: pendingChanges,
      insertions: summary.insertions,
      deletions: summary.deletions,
      note: summary.note,
    }),
  )
}

async function main() {
  const [mode] = process.argv.slice(2)
  const args = parseArgs(process.argv.slice(3))

  try {
    if (mode === 'read') {
      await readMode(args.input)
      return
    }
    if (mode === 'build') {
      await buildMode(args.input, args.operations, args.output, args.author)
      return
    }
    fail(`Unsupported mode: ${String(mode)}`)
  } catch (error) {
    fail(error instanceof Error ? error.message : String(error))
  }
}

await main()
