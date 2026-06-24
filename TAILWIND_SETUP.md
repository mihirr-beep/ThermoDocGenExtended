# Tailwind CSS Production Setup Guide

## Overview
Your application has been migrated from Tailwind CSS CDN to a production-ready setup using the Tailwind CLI.

## What Was Changed

### 1. **Removed CDN Dependency**
   - Removed: `<script src="https://cdn.tailwindcss.com"></script>`
   - Replaced with: `<link rel="stylesheet" href="{{ url_for('static', filename='css/output.css') }}">`
   - File: [templates/base.html](../templates/base.html#L11)

### 2. **Created Project Configuration Files**
   - `package.json` - Node.js dependencies and build scripts
   - `tailwind.config.js` - Tailwind CSS configuration
   - `postcss.config.js` - PostCSS configuration for CSS processing
   - `static/css/input.css` - Tailwind directives
   - `static/css/output.css` - Compiled production CSS (auto-generated)

## Production Build Setup

### Prerequisites
- Node.js (v18 or higher) - Already installed
- npm - Comes with Node.js

### Build Commands

**Initial Setup (one-time):**
```bash
npm install --strict-ssl=false
```

**Build CSS for Production:**
```bash
npm run build:css
```

**Watch for Changes During Development:**
```bash
npm run watch:css
```

### Using PowerShell (Windows)

Since the terminal doesn't recognize npm from the PATH immediately after installation, use the full path:

```powershell
# For building:
$env:PATH = "C:\Program Files\nodejs;$env:PATH"
cd "c:\Users\saimounik.chandavolu\OneDrive - Thermo Fisher Scientific\Desktop\Test Workflow - Final"
& ".\node_modules\.bin\tailwindcss.cmd" -i ./static/css/input.css -o ./static/css/output.css

# Or use npm:
& "C:\Program Files\nodejs\npm.cmd" run build:css --strict-ssl=false
```

## How It Works

1. **input.css** - Contains Tailwind directives (`@tailwind base/components/utilities`)
2. **Tailwind CLI** - Scans your HTML templates for class usage
3. **output.css** - Generated minified CSS with only the classes you use
4. **HTML** - References the compiled output.css file via Flask's `url_for()`

## Benefits of This Setup

✅ **Production Optimized** - Only includes CSS for classes you actually use  
✅ **No Runtime Overhead** - CSS is pre-compiled, not generated in the browser  
✅ **Faster Page Loads** - Smaller CSS file size  
✅ **Better Performance** - No JavaScript required for styling  
✅ **Full Customization** - Can extend/modify Tailwind via tailwind.config.js  
✅ **Build Tools Ready** - Set up for integration with other build processes  

## File Structure

```
project/
├── static/
│   └── css/
│       ├── input.css          (Tailwind directives)
│       └── output.css         (Generated production CSS)
├── templates/
│   └── base.html              (Uses output.css)
├── package.json               (npm config)
├── tailwind.config.js         (Tailwind settings)
├── postcss.config.js          (PostCSS plugins)
└── node_modules/              (Dependencies)
```

## Customization

Edit `tailwind.config.js` to:
- Extend colors, spacing, fonts
- Add custom themes
- Configure plugins
- Adjust content paths

Example:
```javascript
module.exports = {
  content: [
    "./templates/**/*.html",
    // Add other paths as needed
  ],
  theme: {
    extend: {
      colors: {
        brand: '#your-color',
      },
    },
  },
  plugins: [],
}
```

## Important Notes

⚠️ **After Configuration Changes:**
- Rebuild CSS: `npm run build:css`
- Restart your Flask server to serve the updated CSS

⚠️ **New HTML Files:**
- If you add new template folders, update the `content` array in `tailwind.config.js`
- Rebuild CSS to include new classes

⚠️ **Deployment:**
- Include `output.css` in your deployment (it's in static/ folder)
- Don't need to deploy node_modules to production
- Do include package.json and tailwind.config.js for rebuilding if needed

## SSL Certificate Issues

If you see SSL errors during npm install:
```bash
npm install --strict-ssl=false
```

This is a common issue in corporate networks. The `--strict-ssl=false` flag disables SSL verification for npm registry access.

## Next Steps

1. ✅ CSS is built and ready
2. ✅ HTML updated to use compiled CSS
3. Run your Flask app and verify styling still works
4. When updating styles: edit HTML/templates, rebuild CSS (`npm run build:css`)
5. (Optional) Add build step to your deployment process
