import { HTMLProps, useCallback, useEffect, useRef, useState } from 'react'
import { AudioData } from '@/lib/audio-data'
import { useAudioReady } from '@/lib/lyrics-review/hooks/useAudioReady'

export interface WaveformVisualizerProps extends Omit<HTMLProps<HTMLCanvasElement>, 'width' | 'height'> {
	startTime: number
	endTime: number
	audioData: AudioData | null
}

export const WaveformVisualizer = ({
	startTime,
	endTime,
	audioData,
	className,
	... canvasProps
}: WaveformVisualizerProps) => {
	const containerRef = useRef<HTMLDivElement | null>(null)
	const canvasCtxRef = useRef<CanvasRenderingContext2D | null>(null)
	const audioReady = useAudioReady()
	const [canvasSize, setCanvasSize] = useState<{ width: number; height: number; }>({ width: 0, height: 0 })

	useEffect(() => {
		if (!containerRef.current) return

		const resizeObserver = new ResizeObserver((entries) => {
			const size = entries.at(-1)!.contentBoxSize[0]
			setCanvasSize({
				width: size.inlineSize,
				height: size.blockSize
			})
		})
		resizeObserver.observe(containerRef.current, {
			box: 'content-box'
		})

		return () => {
			resizeObserver.disconnect()
		}
	}, [containerRef.current])

	const readAudioData = useCallback((startTime: number, endTime: number) => {
		if (audioData == null) return null

		const startIndex = Math.floor(startTime / audioData.duration * audioData.buffer.length)
		const endIndex = Math.floor(endTime / audioData.duration * audioData.buffer.length)
		return audioData.buffer.slice(startIndex, endIndex)
	}, [audioData])

	useEffect(() => {
		render(startTime, endTime)
	}, [audioReady.ready, startTime, endTime, canvasSize])

	const render = (startTime: number, endTime: number) => {
		const ctx = canvasCtxRef.current
		if (!ctx) return

		ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height)

		ctx.fillStyle = 'orange'

		const audioData = readAudioData(startTime, endTime)
		if (!audioData) return

		for (let x = 0; x < ctx.canvas.width; x++) {
			const dataPoint = audioData[Math.floor(x / ctx.canvas.width * audioData.length)]
			const dataPointHeight = dataPoint * ctx.canvas.height

			ctx.fillRect(x, (ctx.canvas.height - dataPointHeight) / 2, 1, dataPointHeight)
		}
	}

	return (
		<div
			ref={containerRef}
			className={className}
		>
			<canvas
				ref={(el) => {
					canvasCtxRef.current = el?.getContext('2d') ?? null
				}}
				className={"w-[100%] h-[100%]"}
				width={canvasSize.width}
				height={canvasSize.height}
				{... canvasProps}
			></canvas>
		</div>
	)
}
