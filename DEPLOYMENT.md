# 🚀 PhunParty Web Host UI Deployment Guide

This guide covers multiple deployment options for the PhunParty Web Host UI.

## 📋 Prerequisites

- Node.js 18+ installed
- Git repository set up
- Built application (`npm run build`)

## 🌐 Deployment Options

### 1. Netlify (Recommended for Static Hosting)

#### Option A: Continuous Deployment (Recommended)
1. **Connect your repository to Netlify:**
   - Go to [Netlify](https://netlify.com)
   - Click "New site from Git"
   - Connect your GitHub repository
   - Set build settings:
     - **Build command:** `npm run build`
     - **Publish directory:** `web-host-ui/build`
     - **Base directory:** `web-host-ui`

2. **Configure environment variables** (if needed):
   - In Netlify dashboard → Site settings → Environment variables

#### Option B: Manual Deployment
```bash
# Install Netlify CLI
npm install -g netlify-cli

# Deploy
cd web-host-ui
npm run build
netlify deploy --prod --dir=build
```

### 2. Vercel

#### Option A: Continuous Deployment
1. **Connect to Vercel:**
   - Go to [Vercel](https://vercel.com)
   - Import your GitHub repository
   - Vercel will auto-detect the React app

#### Option B: Manual Deployment
```bash
# Install Vercel CLI
npm install -g vercel

# Deploy
cd web-host-ui
npm run build
vercel --prod
```

### 3. GitHub Pages

#### Setup GitHub Actions for automatic deployment:
1. **Go to your repository settings → Pages**
2. **Set source to "GitHub Actions"**
3. **The provided workflow will handle deployment**

### 4. Docker Deployment

#### Build and run locally:
```bash
# Build the Docker image
docker build -f Dockerfile.web -t phunparty-web-host .

# Run the container
docker run -p 3000:80 phunparty-web-host
```

#### Using Docker Compose:
```bash
# Run the entire stack
docker-compose up -d
```

### 5. Self-hosted/VPS

#### Using our deployment script:
```bash
# Linux/macOS
chmod +x deploy-web.sh
./deploy-web.sh

# Windows
deploy-web.bat
```

#### Manual deployment to your server:
```bash
# Build the app
cd web-host-ui
npm ci
npm run build

# Copy build/ directory to your web server
# Example with nginx:
sudo cp -r build/* /var/www/html/
```

## 🔧 Configuration Files

- **`netlify.toml`** - Netlify configuration
- **`vercel.json`** - Vercel configuration  
- **`Dockerfile.web`** - Docker configuration
- **`docker/nginx.conf`** - Nginx configuration for Docker
- **`.github/workflows/deploy-web.yml`** - GitHub Actions workflow

## 🔐 Environment Variables

If your app needs environment variables, create them in your deployment platform:

```env
REACT_APP_API_URL=https://api.yourbackend.com
REACT_APP_WEBSOCKET_URL=wss://api.yourbackend.com
```

**Note:** Only variables prefixed with `REACT_APP_` are available in React builds.

## 📊 Performance & Monitoring

### Build Optimization
- The build process automatically optimizes for production
- Static assets are minified and fingerprinted
- Code splitting is enabled

### Monitoring
- Set up monitoring on your chosen platform
- Monitor Core Web Vitals
- Set up error tracking (Sentry, LogRocket, etc.)

## 🚨 Troubleshooting

### Common Issues:

1. **404 on refresh:** 
   - Ensure SPA routing is configured (handled in our configs)

2. **Build fails:**
   - Check Node.js version (should be 18+)
   - Clear cache: `npm ci`

3. **Environment variables not working:**
   - Ensure they're prefixed with `REACT_APP_`
   - Rebuild after adding new variables

### Getting Help:
- Check deployment platform logs
- Verify build process locally first
- Ensure all dependencies are in `package.json`

## 🔄 CI/CD Pipeline

The included GitHub Actions workflow automatically:
- ✅ Runs tests
- ✅ Builds the application  
- ✅ Deploys to your chosen platform
- ✅ Only triggers on changes to `web-host-ui/`

## 🎯 Quick Start Commands

```bash
# Quick Netlify deploy
npm run deploy:netlify

# Quick Vercel deploy  
npm run deploy:vercel

# Local preview of production build
npm run serve
```

---

Choose the deployment method that best fits your needs. Netlify and Vercel are great for getting started quickly, while Docker gives you more control for complex deployments.
