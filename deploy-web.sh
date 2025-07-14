#!/bin/bash

# PhunParty Web Host UI Deployment Script
# This script builds and deploys the web host UI to various platforms

set -e  # Exit on any error

echo "🎉 PhunParty Web Host UI Deployment Script"
echo "=========================================="

# Check if we're in the right directory
if [ ! -f "web-host-ui/package.json" ]; then
    echo "❌ Error: Please run this script from the project root directory"
    exit 1
fi

# Navigate to web-host-ui directory
cd web-host-ui

echo "📦 Installing dependencies..."
npm ci

echo "🧪 Running tests..."
npm test -- --coverage --watchAll=false

echo "🏗️  Building application..."
npm run build

echo "✅ Build completed successfully!"
echo "📁 Build files are in: $(pwd)/build"

# Deployment options
echo ""
echo "🚀 Deployment Options:"
echo "1. Deploy to Netlify (requires Netlify CLI)"
echo "2. Deploy to Vercel (requires Vercel CLI)"
echo "3. Build Docker image"
echo "4. Just build (already done)"
echo ""

read -p "Choose deployment option (1-4): " choice

case $choice in
    1)
        echo "🌐 Deploying to Netlify..."
        if command -v netlify &> /dev/null; then
            netlify deploy --prod --dir=build
        else
            echo "❌ Netlify CLI not found. Install with: npm install -g netlify-cli"
            exit 1
        fi
        ;;
    2)
        echo "🔺 Deploying to Vercel..."
        if command -v vercel &> /dev/null; then
            vercel --prod
        else
            echo "❌ Vercel CLI not found. Install with: npm install -g vercel"
            exit 1
        fi
        ;;
    3)
        echo "🐳 Building Docker image..."
        cd ..
        docker build -f Dockerfile.web -t phunparty-web-host .
        echo "✅ Docker image 'phunparty-web-host' built successfully!"
        echo "Run with: docker run -p 3000:80 phunparty-web-host"
        ;;
    4)
        echo "✅ Build completed. Files ready for manual deployment."
        ;;
    *)
        echo "❌ Invalid option selected."
        exit 1
        ;;
esac

echo ""
echo "🎉 Deployment process completed!"
