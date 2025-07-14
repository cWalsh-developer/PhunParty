@echo off
REM PhunParty Web Host UI Deployment Script for Windows
REM This script builds and deploys the web host UI to various platforms

echo 🎉 PhunParty Web Host UI Deployment Script
echo ==========================================

REM Check if we're in the right directory
if not exist "web-host-ui\package.json" (
    echo ❌ Error: Please run this script from the project root directory
    exit /b 1
)

REM Navigate to web-host-ui directory
cd web-host-ui

echo 📦 Installing dependencies...
call npm ci
if %errorlevel% neq 0 exit /b %errorlevel%

echo 🧪 Running tests...
call npm test -- --coverage --watchAll=false
if %errorlevel% neq 0 exit /b %errorlevel%

echo 🏗️  Building application...
call npm run build
if %errorlevel% neq 0 exit /b %errorlevel%

echo ✅ Build completed successfully!
echo 📁 Build files are in: %cd%\build

echo.
echo 🚀 Deployment Options:
echo 1. Deploy to Netlify (requires Netlify CLI)
echo 2. Deploy to Vercel (requires Vercel CLI)
echo 3. Build Docker image
echo 4. Just build (already done)
echo.

set /p choice="Choose deployment option (1-4): "

if "%choice%"=="1" (
    echo 🌐 Deploying to Netlify...
    where netlify >nul 2>nul
    if %errorlevel% neq 0 (
        echo ❌ Netlify CLI not found. Install with: npm install -g netlify-cli
        exit /b 1
    )
    call netlify deploy --prod --dir=build
) else if "%choice%"=="2" (
    echo 🔺 Deploying to Vercel...
    where vercel >nul 2>nul
    if %errorlevel% neq 0 (
        echo ❌ Vercel CLI not found. Install with: npm install -g vercel
        exit /b 1
    )
    call vercel --prod
) else if "%choice%"=="3" (
    echo 🐳 Building Docker image...
    cd ..
    docker build -f Dockerfile.web -t phunparty-web-host .
    if %errorlevel% neq 0 exit /b %errorlevel%
    echo ✅ Docker image 'phunparty-web-host' built successfully!
    echo Run with: docker run -p 3000:80 phunparty-web-host
) else if "%choice%"=="4" (
    echo ✅ Build completed. Files ready for manual deployment.
) else (
    echo ❌ Invalid option selected.
    exit /b 1
)

echo.
echo 🎉 Deployment process completed!
