import { Mark, mergeAttributes } from '@tiptap/core'

/**
 * Inline tracked insertion mark.
 * Renders as <span class="tracked-change tracked-change--insertion"
 *   data-change-id data-change-type="insertion"> so the existing
 * OfficeReviewEditor hover/sidebar/decision logic (which keys off
 * [data-change-id]) keeps working unchanged.
 *
 * MIT — ported from pawansoni007/tiptap-track-changes-extension-free.
 */
export const InsertionMark = Mark.create({
  name: 'insertion',
  inclusive: true,
  exitable: true,

  addAttributes() {
    return {
      changeId: { default: null },
      createdAt: { default: null },
    }
  },

  parseHTML() {
    return [{ tag: 'span[data-change-type="insertion"]' }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      'span',
      mergeAttributes(HTMLAttributes, {
        class: 'tracked-change tracked-change--insertion',
        'data-change-id': HTMLAttributes.changeId,
        'data-change-type': 'insertion',
      }),
      0,
    ]
  },
})
