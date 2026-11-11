import { Mark, mergeAttributes } from '@tiptap/core'

/**
 * Inline tracked deletion mark.
 * Renders as <span class="tracked-change tracked-change--deletion"
 *   data-change-id data-change-type="deletion">.
 *
 * MIT — ported from pawansoni007/tiptap-track-changes-extension-free.
 */
export const DeletionMark = Mark.create({
  name: 'deletion',
  inclusive: true,
  exitable: true,

  addAttributes() {
    return {
      changeId: { default: null },
      createdAt: { default: null },
    }
  },

  parseHTML() {
    return [{ tag: 'span[data-change-type="deletion"]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'span',
      mergeAttributes(HTMLAttributes, {
        class: 'tracked-change tracked-change--deletion',
        'data-change-id': HTMLAttributes.changeId,
        'data-change-type': 'deletion',
      }),
      0,
    ]
  },
})
