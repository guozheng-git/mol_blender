// Decode actual presentation pixels, not merely the sample count: AVFoundation
// accepts RGB H.264 streams whose colours it nevertheless decodes incorrectly.
import Foundation
import AVFoundation
import CoreVideo
let asset = AVURLAsset(url: URL(fileURLWithPath: CommandLine.arguments[1]))
let track = asset.tracks(withMediaType: .video).first!
let reader = try AVAssetReader(asset: asset)
let output = AVAssetReaderTrackOutput(track: track, outputSettings:
    [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
reader.add(output)
reader.startReading()
var colours = [[Int]]()
while let sample = output.copyNextSampleBuffer(), let buffer = CMSampleBufferGetImageBuffer(sample) {
    CVPixelBufferLockBaseAddress(buffer, .readOnly)
    let stride = CVPixelBufferGetBytesPerRow(buffer)
    let pixels = CVPixelBufferGetBaseAddress(buffer)!.assumingMemoryBound(to: UInt8.self)
    let offset = (CVPixelBufferGetHeight(buffer) / 2) * stride + (CVPixelBufferGetWidth(buffer) / 2) * 4
    colours.append([Int(pixels[offset + 2]), Int(pixels[offset + 1]), Int(pixels[offset])])
    CVPixelBufferUnlockBaseAddress(buffer, .readOnly)
}
assert(reader.status == .completed)
let data = try JSONSerialization.data(withJSONObject: colours)
print(String(data: data, encoding: .utf8)!)
