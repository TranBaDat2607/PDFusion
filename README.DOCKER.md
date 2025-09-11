# Docker Setup for Desktop PDF Translator

This document explains how to run the Desktop PDF Translator application in a Docker container.

## Prerequisites

1. Docker Desktop installed on your system
2. API keys for either OpenAI or Google Gemini translation services

## Building the Docker Image

To build the Docker image, run:

```bash
docker build -t desktop-pdf-translator .
```

Or using docker-compose:

```bash
docker-compose build
```

## Running the Application

### Method 1: Using Docker Compose (Recommended)

1. Create a `.env` file with your API keys:
   ```
   OPENAI_API_KEY=your_openai_api_key_here
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

2. Run the application:
   ```bash
   docker-compose up
   ```

### Method 2: Using Docker Run

```bash
docker run -it --rm \
  -e OPENAI_API_KEY=your_openai_api_key_here \
  -e GEMINI_API_KEY=your_gemini_api_key_here \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/translated_pdfs:/app/translated_pdfs \
  -v $(pwd)/config:/app/config \
  -p 5900:5900 \
  desktop-pdf-translator
```

## Accessing the GUI

Since this is a GUI application running in Docker, accessing the interface requires special handling:

1. **VNC Access**: The container exposes port 5900 for VNC access. You can connect to the GUI using a VNC viewer:
   - Host: localhost
   - Port: 5900
   - No password required

2. **X11 Forwarding** (Linux only): If running on Linux with X11, you can use X11 forwarding:
   ```bash
   xhost +local:docker
   docker run -it --rm \
     -e DISPLAY=$DISPLAY \
     -v /tmp/.X11-unix:/tmp/.X11-unix \
     -e OPENAI_API_KEY=your_openai_api_key_here \
     desktop-pdf-translator
   ```

## Configuration

The application can be configured using environment variables:

- `OPENAI_API_KEY`: Your OpenAI API key (required for OpenAI translation)
- `GEMINI_API_KEY`: Your Google Gemini API key (required for Gemini translation)
- `DEBUG_MODE`: Set to "true" to enable debug logging

## Volumes

The following directories are mounted as volumes:

- `/app/logs`: Application logs
- `/app/translated_pdfs`: Translated PDF files
- `/app/config`: Configuration files

## Troubleshooting

### GUI Not Displaying

If you can't see the GUI:

1. Ensure VNC server is running in the container
2. Connect using a VNC viewer to localhost:5900
3. Check that port 5900 is not blocked by firewall

### Translation Service Errors

If you see translation service errors:

1. Verify your API keys are correct
2. Check that you have internet access from the container
3. Ensure your API keys have the necessary permissions

### Permission Issues

If you encounter permission issues with mounted volumes:

1. Check that the directories exist on the host
2. Ensure proper read/write permissions
3. Consider running the container with adjusted user permissions

## Building for Different Architectures

To build for a specific architecture:

```bash
docker buildx build --platform linux/amd64 -t desktop-pdf-translator .
```