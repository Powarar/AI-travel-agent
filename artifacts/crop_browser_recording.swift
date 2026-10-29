import AVFoundation

let root = URL(fileURLWithPath: CommandLine.arguments[1])
let source = root.appendingPathComponent("okuda-browser-run-full-v2.mov")
let output = root.appendingPathComponent("okuda-browser-flow-v2.mp4")
let asset = AVURLAsset(url: source)
let sourceTrack = try await asset.loadTracks(withMediaType: .video).first!
let duration = try await asset.load(.duration)

let composition = AVMutableComposition()
let compositionTrack = composition.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)!
try compositionTrack.insertTimeRange(CMTimeRange(start: .zero, duration: duration), of: sourceTrack, at: .zero)

let instruction = AVMutableVideoCompositionInstruction()
instruction.timeRange = CMTimeRange(start: .zero, duration: duration)
let layer = AVMutableVideoCompositionLayerInstruction(assetTrack: compositionTrack)
layer.setTransform(CGAffineTransform(translationX: -1800, y: -200), at: .zero)
instruction.layerInstructions = [layer]

let videoComposition = AVMutableVideoComposition()
videoComposition.instructions = [instruction]
videoComposition.renderSize = CGSize(width: 660, height: 1500)
videoComposition.frameDuration = CMTime(value: 1, timescale: 30)

try? FileManager.default.removeItem(at: output)
let exporter = AVAssetExportSession(asset: composition, presetName: AVAssetExportPresetHighestQuality)!
exporter.outputURL = output
exporter.outputFileType = .mp4
exporter.videoComposition = videoComposition
try await exporter.export(to: output, as: .mp4)
print(output.path)
