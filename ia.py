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


def iniciar_deteccion(model_path='yolov8n.pt', intervalo=10, output_folder='frames_detectados'):
    """
    Inicia la detección de personas en tiempo real con YOLO.
    Retorna el número de personas detectadas en cada intervalo.
    """
    # Cargar modelo YOLO
    model = YOLO(model_path)

    # Crear carpeta para guardar frames
    os.makedirs(output_folder, exist_ok=True)

    # Abrir cámara
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ No se pudo acceder a la cámara.")
        return

    last_time = time.time()
    frame_id = 0

    print("🎥 Detección iniciada... Presiona 'q' para salir.\n")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("⚠️ No se pudo leer el frame de la cámara.")
            break

        current_time = time.time()

        # Detección cada cierto intervalo
        if current_time - last_time >= intervalo:
            last_time = current_time

            results = model(frame)
            num_personas = (results[0].boxes.cls == 0).sum().item()

            print(f"[{time.strftime('%H:%M:%S')}] {num_personas} personas detectadas.")
            print(estado_micro(num_personas))

            # Dibujar detecciones y guardar
            annotated_frame = results[0].plot()
            save_path = os.path.join(output_folder, f"frame_{frame_id:04d}.jpg")
            cv2.imwrite(save_path, annotated_frame)
            print(f"🖼️ Frame guardado en: {save_path}\n")

            frame_id += 1

        cv2.imshow("Detección de personas (YOLOv8)", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("✅ Detección finalizada. Frames guardados en:", output_folder)


# Solo se ejecuta si se ejecuta directamente este archivo (no al importarlo)
if __name__ == "__main__":
    iniciar_deteccion()

