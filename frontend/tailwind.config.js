import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('.', import.meta.url))

/** @type {import('tailwindcss').Config} */
export default {
  content: [`${root}index.html`, `${root}src/**/*.{js,ts,jsx,tsx}`],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui'],
        mono: ['IBM Plex Mono', 'ui-monospace'],
      },
      colors: {
        ink: '#07111f',
        panel: '#0c1929',
        line: '#20334a',
        cyan: '#47d7e8',
        mint: '#65e6b4',
        amber: '#f3b84b',
      },
    },
  },
  plugins: [],
}
