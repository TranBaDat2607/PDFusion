@echo off
echo Setting up API keys for Desktop PDF Translator
echo.

echo Please enter your API keys (leave blank if you don't have one):
echo.

set /p OPENAI_KEY="OpenAI API Key: "
set /p GEMINI_KEY="Google Gemini API Key: "

echo.
echo Setting environment variables...

if not "%OPENAI_KEY%"=="" (
    setx OPENAI_API_KEY "%OPENAI_KEY%"
    echo ✓ OpenAI API key configured
)

if not "%GEMINI_KEY%"=="" (
    setx GEMINI_API_KEY "%GEMINI_KEY%"
    echo ✓ Gemini API key configured
)

echo.
echo API keys have been set for future sessions.
echo Please restart the application or open a new command prompt.
echo.
pause