import type { ContentBlock, Message } from '../../types'

interface Props {
  message: Message
}

function contentToText(content: string | ContentBlock[]): string {
  if (typeof content === 'string') return content
  return content
    .filter((b): b is { type: 'text'; text: string } => b.type === 'text')
    .map((b) => b.text)
    .join('\n')
}

export default function SystemMessage({ message }: Props) {
  return (
    <div className="flex w-full mb-4 justify-center">
      <span className="text-[length:var(--fs-sm)] italic text-[var(--c-text-secondary)] px-3 py-1 rounded-[var(--radius-md)] bg-[var(--c-surface)]/40 max-w-[80%] text-center">
        {contentToText(message.content)}
      </span>
    </div>
  )
}
