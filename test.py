import cv2
import os
import time
from ultralytics import YOLO

def estado_micro(x):
    if x <= 20:
        return "Asientos disponibles"
    if x <= 30:
        return "Pasillo disponible"
    if x > 30:
        return "Llena"

# Cargar modelo YOLO
model = YOLO('yolov8n.pt')  # Puedes cambiar a 'yolov8s.pt' o 'yolov9c.pt' si quieres más precisión

# Carpeta donde guardar los frames detectados
output_folder = 'frames_detectados'
os.makedirs(output_folder, exist_ok=True)

# Abrir la webcam (0 = cámara predeterminada)
cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("❌ No se pudo acceder a la cámara.")
    exit()

# Variables de control
last_time = time.time()
interval = 10  # segundos
frame_id = 0

print("🎥 Detección iniciada... Presiona 'q' para salir.\n")

while True:
    ret, frame = cap.read()
    if not ret:
        print("⚠️ No se pudo leer el frame de la cámara.")
        break

    current_time = time.time()

    # Cada 10 segundos realiza detección
    if current_time - last_time >= interval:
        last_time = current_time

        # Analizar frame con YOLO
        results = model(frame)
        num_personas = (results[0].boxes.cls == 0).sum().item()

        # Mostrar conteo
        print(f"[{time.strftime('%H:%M:%S')}] {num_personas} personas detectadas.")

        print(estado_micro(num_personas))

        # Dibujar detecciones
        annotated_frame = results[0].plot()

        # Guardar frame con detecciones
        save_path = os.path.join(output_folder, f"frame_{frame_id:04d}.jpg")
        cv2.imwrite(save_path, annotated_frame)
        print(f"🖼️ Frame guardado en: {save_path}\n")

        frame_id += 1

    # Mostrar vista en tiempo real (sin detección cada frame)
    cv2.imshow("Detección de personas (YOLOv8)", frame)

    # Salir con la tecla 'q'
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
print("✅ Detección finalizada. Frames guardados en:", output_folder)

