# Circle Detection & Geometry Capture Guide

## Overview

CamScan now supports **circle detection and measurement** for capturing curved surfaces and geometry that can be imported into CAD/modeling software like AutoCAD, FreeCAD, Blender, and Fusion 360.

## Features

### 🔵 Circle Measurement Tools

1. **Three-Point Circle** - Click 3 points on a circular edge to fit a circle
2. **Auto-Detection** - Automatically detect circles in the image
3. **Manual Circle** - Draw a circle by center + radius

### 📏 Measurements Provided

For each circle, CamScan calculates:
- **Diameter** (in selected units: mm, cm, in)
- **Radius** (in mm)
- **Circumference** (in mm)
- **Area** (in mm²)
- **Center coordinates** (pixels and real-world)

### 📤 Export Formats

#### **1. DXF (AutoCAD Drawing Exchange Format)**
- **Use for**: AutoCAD, FreeCAD, QCAD, LibreCAD, Fusion 360
- **Contains**: Precise geometry in real-world units
- **Layers**: Circles, Lines, Rectangles, Polylines
- **Import directly** into CAD software for 3D modeling

#### **2. CSV (Spreadsheet)**
- **Use for**: Excel, Google Sheets, data analysis
- **Contains**: Measurements table with:
  - Type (Circle, Line, Rectangle, etc.)
  - Diameter/Length/Width
  - Area
  - Units
  - Notes

#### **3. JSON (Data format)**
- **Use for**: Custom processing, scripting, data exchange
- **Contains**: Complete geometry data with full precision

#### **4. PNG (Annotated Image)**
- **Use for**: Documentation, reports, visual reference
- **Contains**: Original image + overlays + measurements

## How to Use

### Step 1: Calibrate Your Image

1. Place a calibration marker in your photo
2. Upload image to CamScan
3. Calibration is automatically detected

### Step 2: Add Circle Annotations

**Method A: Three-Point Circle (Recommended)**
1. Click the **Circle Tool** (⭕) in toolbar
2. Click 3 points on the edge of a circular object
3. Circle is automatically fitted to those points
4. Measurements appear instantly

**Method B: Manual Circle**
1. Click the **Circle Tool**
2. Click and drag to draw a circle
3. Adjust size as needed

### Step 3: Export to CAD Software

**For 3D Modeling (DXF Export):**
1. Click **Export** → **DXF**
2. Save the `.dxf` file
3. Import into your CAD software:

   **AutoCAD/Fusion 360:**
   ```
   File → Import → Select DXF file
   ```

   **FreeCAD:**
   ```
   File → Import → Select DXF file
   All geometry appears on separate layers
   ```

   **Blender:**
   ```
   Install DXF Importer add-on
   File → Import → AutoCAD DXF → Select file
   ```

**For Data Analysis (CSV Export):**
1. Click **Export** → **CSV**
2. Open in Excel/Sheets
3. Analyze measurements, create charts, etc.

## Use Cases

### 🔩 Mechanical Parts
- Measure bolt holes, shafts, bearings
- Export to CAD for reverse engineering
- Create replacement parts

### 🏺 Product Design
- Capture curves from physical prototypes
- Measure circular features
- Import into 3D modeling software

### 📐 Architecture
- Measure circular columns, arches
- Document curved surfaces
- Create as-built drawings

### 🎨 Art & Crafts
- Measure circular patterns
- Create templates for cutting
- Scale designs accurately

## Python API (Advanced)

### Detect Circles Programmatically

```python
from circle_detection import detect_circles_hough, detect_circles_contour
import cv2

# Load image
img = cv2.imread('photo.jpg')

# Method 1: Hough Circle Transform (fast, good for clear circles)
circles = detect_circles_hough(
    img,
    min_radius=10,
    max_radius=500,
    debug=True
)
# Returns: [(center_x, center_y, radius), ...]

# Method 2: Contour Analysis (more robust, gives quality metrics)
circles = detect_circles_contour(
    img,
    min_radius=10,
    max_radius=500,
    min_circularity=0.7,  # 0-1, higher = more circular
    debug=True
)
# Returns: [{"center": (x,y), "radius": r, "circularity": c, ...}, ...]
```

### Export to DXF

```python
from circle_detection import export_to_dxf

geometry = [
    {
        "type": "circle",
        "center_x": 100,
        "center_y": 100,
        "radius_px": 50,
        # ... other fields
    },
    {
        "type": "line",
        "x1": 0, "y1": 0,
        "x2": 100, "y2": 100
    }
]

# Calibration: 1 pixel = 0.5mm
mm_per_px = 0.5

export_to_dxf(geometry, "output.dxf", mm_per_px)
```

## JavaScript API (Web UI Customization)

```javascript
// Access circle tools
const Circles = window.CalibCircles;

// Create a circle annotation
const circle = Circles.createCircle(
    [centerX, centerY],  // center point
    radius,              // in pixels
    mm_per_px            // calibration scale
);

// Fit circle to 3 points
const points = [[x1, y1], [x2, y2], [x3, y3]];
const fitted = Circles.fitCircleFromPoints(points);
// Returns: {center: [x, y], radius: r}

// Draw circle on canvas
Circles.drawCircle(ctx, canvas, circle, data, 'mm', isSelected);

// Export circle to JSON
const json = Circles.circleToJSON(circle, data, 'mm');

// Check if point is on/in circle
const onEdge = Circles.isPointOnCircle(mouseX, mouseY, circle, tolerance);
const inside = Circles.isPointInCircle(mouseX, mouseY, circle);
```

## Troubleshooting

### Circles Not Detected?

1. **Increase contrast** - Ensure good lighting
2. **Clean edges** - Remove background clutter
3. **Adjust parameters**:
   ```python
   # More sensitive detection
   circles = detect_circles_hough(img, param2=20)  # Lower = more circles

   # More permissive circularity
   circles = detect_circles_contour(img, min_circularity=0.6)
   ```

### DXF Export Not Working?

1. **Install ezdxf**:
   ```bash
   pip install ezdxf==1.3.4
   ```

2. **Check backend endpoint**: Visit `http://localhost:8059/api/export/dxf`
   - Should return an error (not 404)

3. **Fallback**: Export as JSON, convert manually

### Measurements Seem Off?

1. **Verify calibration** - Ensure calibration marker is detected
2. **Check units** - Switch between mm/cm/in
3. **Re-calibrate** - Upload new image with calibration marker

## Technical Details

### Circle Detection Algorithms

**Hough Circle Transform:**
- **Pros**: Fast, good for perfect circles
- **Cons**: Sensitive to noise, parameters need tuning
- **Best for**: Clear photos with distinct circular edges

**Contour-Based Detection:**
- **Pros**: Robust to noise, provides quality metrics
- **Cons**: Slower, may miss partial circles
- **Best for**: Complex scenes, imperfect circles

### DXF Format Specifications

- **Version**: AutoCAD R2010
- **Units**: Millimeters (mm)
- **Coordinate System**: 2D (X, Y)
- **Layers**:
  - `CIRCLES` - All circular geometry
  - `LINES` - Linear measurements
  - `RECTANGLES` - Rectangular geometry
  - `POLYLINES` - Multi-segment paths

### Accuracy

- **Calibration accuracy**: ±0.5mm (with good calibration marker)
- **Circle detection**: ±2px center, ±1px radius
- **Real-world accuracy**: ±1mm diameter for objects >50mm

## Examples

### Example 1: Measure Bottle Cap
```
1. Place calibration marker next to bottle cap
2. Take photo
3. Upload to CamScan
4. Use 3-point circle tool on cap edge
5. Read diameter: 28.5mm
6. Export to DXF for 3D printing custom cap
```

### Example 2: Reverse Engineer Gear
```
1. Photo gear with calibration marker
2. Measure outer circle (pitch diameter)
3. Measure center hole
4. Count teeth manually
5. Export DXF with both circles
6. Import to CAD, add teeth geometry
```

### Example 3: Architectural Column
```
1. Photo column with measuring tape for calibration
2. Detect circles at top, middle, bottom
3. Compare diameters (check for taper)
4. Export measurements to CSV
5. Create spreadsheet with taper analysis
```

## Future Enhancements

Planned features:
- [ ] Arc detection (partial circles)
- [ ] Ellipse detection
- [ ] Automatic circle detection on upload
- [ ] Bulk measurement mode
- [ ] STEP file export (3D format)
- [ ] Advanced curve fitting (splines, Bezier)

## Support

For issues or feature requests:
1. Check console for error messages (F12)
2. Verify browser supports HTML5 Canvas
3. Ensure good lighting in photos
4. Report issues with example images

---

**Happy Scanning! 📸📐**
