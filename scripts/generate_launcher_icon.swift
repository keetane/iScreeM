import AppKit
import Foundation

private struct IconVariant {
    let filename: String
    let pixels: Int
}

private let variants = [
    IconVariant(filename: "icon_16x16.png", pixels: 16),
    IconVariant(filename: "icon_16x16@2x.png", pixels: 32),
    IconVariant(filename: "icon_32x32.png", pixels: 32),
    IconVariant(filename: "icon_32x32@2x.png", pixels: 64),
    IconVariant(filename: "icon_128x128.png", pixels: 128),
    IconVariant(filename: "icon_128x128@2x.png", pixels: 256),
    IconVariant(filename: "icon_256x256.png", pixels: 256),
    IconVariant(filename: "icon_256x256@2x.png", pixels: 512),
    IconVariant(filename: "icon_512x512.png", pixels: 512),
    IconVariant(filename: "icon_512x512@2x.png", pixels: 1024),
]

private func drawIcon(size: CGFloat) {
    let canvas = NSRect(x: 0, y: 0, width: size, height: size)
    NSColor.clear.setFill()
    canvas.fill()

    let inset = size * 0.055
    let tile = NSBezierPath(
        roundedRect: canvas.insetBy(dx: inset, dy: inset),
        xRadius: size * 0.215,
        yRadius: size * 0.215
    )
    NSColor(calibratedRed: 0.035, green: 0.075, blue: 0.115, alpha: 1).setFill()
    tile.fill()

    let center = NSPoint(x: size * 0.5, y: size * 0.49)
    let radius = size * 0.295
    let ring = NSBezierPath()
    for index in 0..<5 {
        let angle = -CGFloat.pi / 2 + CGFloat(index) * 2 * CGFloat.pi / 5
        let point = NSPoint(
            x: center.x + cos(angle) * radius,
            y: center.y - sin(angle) * radius
        )
        index == 0 ? ring.move(to: point) : ring.line(to: point)
    }
    ring.close()
    ring.lineWidth = max(1, size * 0.058)
    ring.lineJoinStyle = .round
    ring.lineCapStyle = .round
    NSColor(calibratedRed: 0.31, green: 0.86, blue: 0.91, alpha: 1).setStroke()
    ring.stroke()
}

private func pngData(size: Int) -> Data? {
    guard let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: size,
        pixelsHigh: size,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    ) else { return nil }

    bitmap.size = NSSize(width: size, height: size)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    NSGraphicsContext.current?.imageInterpolation = .high
    drawIcon(size: CGFloat(size))
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])
}

guard CommandLine.arguments.count == 2 else {
    fputs("Usage: generate_launcher_icon.swift OUTPUT.iconset\n", stderr)
    exit(2)
}

let output = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
for variant in variants {
    guard let data = pngData(size: variant.pixels) else {
        fputs("Failed to render \(variant.filename)\n", stderr)
        exit(1)
    }
    try data.write(to: output.appendingPathComponent(variant.filename), options: .atomic)
}
