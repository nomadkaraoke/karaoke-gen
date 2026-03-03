import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

// Read version from root pyproject.toml (single source of truth)
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const pyprojectPath = path.join(__dirname, '..', 'pyproject.toml');
let appVersion = '0.0.0';
try {
  const pyprojectContent = fs.readFileSync(pyprojectPath, 'utf-8');
  const versionMatch = pyprojectContent.match(/^version\s*=\s*"([^"]+)"/m);
  if (versionMatch) {
    appVersion = versionMatch[1];
  }
} catch (err) {
  console.warn('Could not read version from pyproject.toml:', err.message);
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    NEXT_PUBLIC_APP_VERSION: appVersion,
  },
  // Only use static export for production builds (not dev)
  output: process.env.NODE_ENV === 'production' ? 'export' : undefined,
  trailingSlash: true,
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
  // Set turbopack root to the frontend directory so it can resolve packages
  // from worktrees (where the path differs from the main clone)
  turbopack: {
    root: __dirname,
  },
  // GitHub Pages serves from subdomain, so no basePath needed
  // gen.nomadkaraoke.com -> nomadkaraoke.github.io/karaoke-gen

  // Proxy API requests to backend in development (avoids CORS)
  async rewrites() {
    // Only apply rewrites in development
    if (process.env.NODE_ENV === 'production') {
      return [];
    }
    const backendUrl = process.env.BACKEND_URL || 'https://api.nomadkaraoke.com';
    // Use beforeFiles to run rewrites before the trailingSlash redirect
    // This ensures /api/* requests go to the backend without trailing slash issues
    return {
      beforeFiles: [
        // Strip trailing slash from API requests before proxying
        {
          source: '/api/:path*/',
          destination: `${backendUrl}/api/:path*`,
        },
        {
          source: '/api/:path*',
          destination: `${backendUrl}/api/:path*`,
        },
        {
          source: '/backend-info',
          destination: `${backendUrl}/`,
        },
      ],
    };
  },
}

export default nextConfig
