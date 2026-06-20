/// <reference types="vitest/config" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8001'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      'shadcn/tailwind.css': path.resolve(__dirname, './node_modules/shadcn/dist/tailwind.css'),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    reporters: ['verbose'],
    exclude: ['e2e/**', 'node_modules/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'text-summary'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/routeTree.gen.ts', 'src/components/ui/**'],
      thresholds: {
        statements: 5,
        branches: 5,
        functions: 5,
        lines: 5,
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 4200,
    strictPort: true,
    proxy: {
      '/api': {
        target: apiProxyTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
