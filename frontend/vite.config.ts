import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileViewerRenderers } from '@file-viewer/vite-plugin'

export default defineConfig({
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
      '@domains': resolve(__dirname, 'subpackages'),
    },
  },
  plugins: [
    react(),
    tailwindcss(),
    fileViewerRenderers({ copyAssets: true }) as any,
  ],
  optimizeDeps: {
    exclude: ['@pierre/theming', '@pierre/diffs'],
  },
  build: {
    rollupOptions: {
      external: [
        '@pierre/diffs/react',
        'react-pdf',
        'write-excel-file/browser',
      ],
      output: {
        manualChunks(id) {
          // file-viewer 的 word/ofd 存在循环依赖，合并到同一 chunk 避免 TDZ 错误
          if (
            id.includes('/renderer-word/') || id.includes('/renderer-ofd/') ||
            id.includes('file-viewer-word') || id.includes('file-viewer-ofd')
          ) {
            return 'file-viewer-word-ofd'
          }
        },
      },
    },
  },
  server: {
    proxy: {
      '/api/ws': {
        target: 'http://localhost:8000',
        ws: true,
        changeOrigin: true,
      },
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
