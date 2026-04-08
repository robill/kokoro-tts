:: **GPU Enablement Start**
:: Set ONNX_PROVIDER environment variable to CUDAExecutionProvider for GPU usage
echo Setting ONNX_PROVIDER=CUDAExecutionProvider for GPU...
set ONNX_PROVIDER=CUDAExecutionProvider
echo ONNX_PROVIDER is now set to: %ONNX_PROVIDER%
:: **GPU Enablement End**