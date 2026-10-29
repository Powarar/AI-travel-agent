import AppKit
import AVFoundation

let root = URL(fileURLWithPath: CommandLine.arguments[1])
let output = root.appendingPathComponent("okuda-demo-flow.mp4")
let paths = ["demo-start.png", "demo-question.png", "demo-plan.png"].map { root.appendingPathComponent($0) }
let images = try paths.map { path -> CGImage in
    guard let image = NSImage(contentsOf: path), let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        throw NSError(domain: "demo", code: 1, userInfo: [NSLocalizedDescriptionKey: "Cannot load \(path.lastPathComponent)"])
    }
    return cgImage
}

let width = images[0].width
let height = images[0].height
let fps: Int32 = 30
let phases: [(image: Int, frames: Int)] = [(0, 75), (1, 75), (2, 135)]
let fades = 24

try? FileManager.default.removeItem(at: output)
let writer = try AVAssetWriter(outputURL: output, fileType: .mp4)
let settings: [String: Any] = [
    AVVideoCodecKey: AVVideoCodecType.h264,
    AVVideoWidthKey: width,
    AVVideoHeightKey: height,
    AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: 4_000_000],
]
let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
let attributes: [String: Any] = [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
    kCVPixelBufferWidthKey as String: width,
    kCVPixelBufferHeightKey as String: height,
]
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: attributes)
writer.add(input)
writer.startWriting()
writer.startSession(atSourceTime: .zero)

func frame(_ first: CGImage, _ second: CGImage?, progress: CGFloat) -> CVPixelBuffer {
    var buffer: CVPixelBuffer?
    CVPixelBufferCreate(kCFAllocatorDefault, width, height, kCVPixelFormatType_32BGRA, attributes as CFDictionary, &buffer)
    let pixelBuffer = buffer!
    CVPixelBufferLockBaseAddress(pixelBuffer, [])
    let context = CGContext(
        data: CVPixelBufferGetBaseAddress(pixelBuffer), width: width, height: height,
        bitsPerComponent: 8, bytesPerRow: CVPixelBufferGetBytesPerRow(pixelBuffer),
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue
    )!
    context.setFillColor(NSColor.black.cgColor)
    context.fill(CGRect(x: 0, y: 0, width: width, height: height))
    context.draw(first, in: CGRect(x: 0, y: 0, width: width, height: height))
    if let second {
        context.setAlpha(progress)
        context.draw(second, in: CGRect(x: 0, y: 0, width: width, height: height))
    }
    CVPixelBufferUnlockBaseAddress(pixelBuffer, [])
    return pixelBuffer
}

var number = 0
for (phase, item) in phases.enumerated() {
    for local in 0..<item.frames {
        while !input.isReadyForMoreMediaData { usleep(1_000) }
        let next = phase + 1 < images.count && local >= item.frames - fades ? images[phase + 1] : nil
        let progress: CGFloat = next == nil ? 0 : CGFloat(local - (item.frames - fades) + 1) / CGFloat(fades)
        let time = CMTime(value: CMTimeValue(number), timescale: fps)
        adaptor.append(frame(images[item.image], next, progress: progress), withPresentationTime: time)
        number += 1
    }
}
input.markAsFinished()
writer.finishWriting {
    if writer.status != .completed {
        let message = writer.error?.localizedDescription ?? "unknown error"
        fputs("Video encoding failed: \(message)\n", stderr)
    }
}
while writer.status == .writing { RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.1)) }
guard writer.status == .completed else { exit(1) }
print(output.path)
