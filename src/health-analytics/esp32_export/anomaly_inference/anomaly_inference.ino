/*
  anomaly_inference.ino
  -----------------------
  Runs the tiny autoencoder anomaly-detection model on an ESP32 using the
  Chirale TensorFlow Lite for Microcontrollers Arduino library.

  What this sketch does each cycle:
    1. Reads heart_rate + steps_in_interval from your sensor code (stubbed
       here -- replace read_heart_rate()/read_steps_in_interval() with your
       actual MAX30102 / accelerometer driver calls).
    2. Normalizes features using the mean/std from models/feature_scaler.json.
    3. Runs the int8 quantized model, computes reconstruction error.
    4. Compares against the threshold from feature_scaler.json.
    5. If it fires, this is a SECONDARY signal -- the primary anomaly
       detector on real deployments should still be the cheap rule-based
       z-score check (baseline.py / anomaly_detection.py ported to C),
       run every sample. This model only needs to run periodically
       (e.g. once per minute) since it's checking a joint pattern, not a
       fast physiological event.
*/

// include main library header file
#include <Chirale_TensorFlowLite.h>

// include static array definition of pre-trained model
#include "model_data.h"

// This TensorFlow Lite Micro Library for Arduino is not similar to standard
// Arduino libraries. These additional header files must be included.
#include "tensorflow/lite/micro/all_ops_resolver.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/schema/schema_generated.h"

// ---- From models/feature_scaler.json (already filled in) ----
const float FEATURE_MEAN[4] = {81.73676300048828f, 21.029739379882812f, -0.0008795579196885228f, -1.3366488019528333e-05f};
const float FEATURE_STD[4]  = {17.400503158569336f, 32.469749450683594f, 0.7074612975120544f, 0.7067485451698303f};
const float ANOMALY_THRESHOLD = 0.9876211881637573f;

// Globals pointers, used to address TensorFlow Lite components.
const tflite::Model* model = nullptr;
tflite::MicroInterpreter* interpreter = nullptr;
TfLiteTensor* input = nullptr;
TfLiteTensor* output = nullptr;

// There is no way to calculate this parameter
// the value is usually determined by trial and errors
// It is the dimension of the memory area used by the TFLite interpreter
// to store tensors and intermediate results
constexpr int kTensorArenaSize = 8 * 1024;  // tiny model -> tiny arena

// Keep aligned to 16 bytes for CMSIS (Cortex Microcontroller Software Interface Standard)
alignas(16) uint8_t tensor_arena[kTensorArenaSize];

float read_heart_rate() { return 72.0f; }   // TODO: wire to real sensor
int read_steps_in_interval() { return 12; } // TODO: wire to real sensor

void setup() {
  // Initialize serial communications and wait for Serial Monitor to be opened
  Serial.begin(115200);
  while(!Serial);

  Serial.println("Anomaly Detection Inference Example");
  Serial.println("Initializing TensorFlow Lite Micro Interpreter...");

  // Map the model into a usable data structure. This doesn't involve any
  // copying or parsing, it's a very lightweight operation.
  model = tflite::GetModel(g_anomaly_autoencoder_model);

  // Check if model and library have compatible schema version,
  // if not, there is a misalignement between TensorFlow version used
  // to train and generate the TFLite model and the current version of library
  if (model->version() != TFLITE_SCHEMA_VERSION) {
    Serial.println("Model provided and schema version are not equal!");
    while(true); // stop program here
  }

  // This pulls in all the TensorFlow Lite operators.
  static tflite::AllOpsResolver resolver;

  // Build an interpreter to run the model with.
  static tflite::MicroInterpreter static_interpreter(
      model, resolver, tensor_arena, kTensorArenaSize);
  interpreter = &static_interpreter;

  // Allocate memory from the tensor_arena for the model's tensors.
  // if an error occurs, stop the program.
  TfLiteStatus allocate_status = interpreter->AllocateTensors();
  if (allocate_status != kTfLiteOk) {
    Serial.println("AllocateTensors() failed");
    while(true); // stop program here
  }

  // Obtain pointers to the model's input and output tensors.
  input = interpreter->input(0);
  output = interpreter->output(0);

  Serial.println("Initialization done.");
  Serial.println("");
}

void loop() {
  float hr = 180.0f;
  int steps = read_steps_in_interval();

  unsigned long now_ms = millis();
  float hour_of_day = (now_ms / 3600000.0f);  // replace with RTC-based hour
  float hour_sin = sin(2.0f * PI * hour_of_day / 24.0f);
  float hour_cos = cos(2.0f * PI * hour_of_day / 24.0f);

  float raw[4] = {hr, (float)steps, hour_sin, hour_cos};
  float normalized[4];
  for (int i = 0; i < 4; i++) {
    normalized[i] = (raw[i] - FEATURE_MEAN[i]) / FEATURE_STD[i];
  }

  // Quantize the input from floating-point to integer
  // because model has been optimized by quantization
  float in_scale = input->params.scale;
  int in_zero = input->params.zero_point;
  for (int i = 0; i < 4; i++) {
    int32_t q = (int32_t)roundf(normalized[i] / in_scale) + in_zero;
    input->data.int8[i] = (int8_t)constrain(q, -128, 127);
  }

  // Run inference, and report if an error occurs
  TfLiteStatus invoke_status = interpreter->Invoke();
  if (invoke_status != kTfLiteOk) {
    Serial.println("Invoke failed!");
    return;
  }

  // Obtain the quantized output from model's output tensor
  // and dequantize to floating-point
  float out_scale = output->params.scale;
  int out_zero = output->params.zero_point;
  float mse = 0.0f;
  for (int i = 0; i < 4; i++) {
    float recon = (output->data.int8[i] - out_zero) * out_scale;
    float diff = recon - normalized[i];
    mse += diff * diff;
  }
  mse /= 4.0f;

  // Print current status
  Serial.print("HR: ");
  Serial.print(hr, 1);
  Serial.print(", Steps: ");
  Serial.print(steps);
  Serial.print(", MSE: ");
  Serial.println(mse, 4);
  
  // Check for anomaly
  if (mse > ANOMALY_THRESHOLD) {
    Serial.print("⚠️ Joint-pattern anomaly flagged, reconstruction_error=");
    Serial.println(mse, 4);
    // TODO: surface this alongside the rule-based anomaly flag, not instead of it.
  }

  delay(60000);  // run this check once a minute
}