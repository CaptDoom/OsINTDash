import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
    plugins: [react()],
    server: {
        host: '0.0.0.0',
        port: 3000,
        proxy: {
            '/api': {
                target: 'http://localhost:3001',
                changeOrigin: true,
            },
            '/ws': {
                target: 'ws://localhost:3001',
                ws: true,
                changeOrigin: true,
            },
        },
    },
    build: {
        chunkSizeWarningLimit: 900,
        rollupOptions: {
            output: {
                manualChunks: function (id) {
                    if (id.includes('node_modules')) {
                        if (id.includes('react')) {
                            return 'vendor-react';
                        }
                        if (id.includes('d3-geo') || id.includes('topojson-client') || id.includes('world-atlas') || id.includes('world-countries')) {
                            return 'vendor-geo';
                        }
                        if (id.includes('cheerio') || id.includes('jsdom') || id.includes('@mozilla/readability')) {
                            return 'vendor-parser';
                        }
                        return 'vendor';
                    }
                },
            },
        },
    },
});
