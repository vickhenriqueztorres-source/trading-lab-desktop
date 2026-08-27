using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Windows.Forms;
using System.Runtime.InteropServices;
using System.Threading;

[assembly: AssemblyTitle("Trading Lab Desktop")]
[assembly: AssemblyDescription("Trading Lab Desktop - Deriv Digit Edge")]
[assembly: AssemblyCompany("Trading Lab Systems")]
[assembly: AssemblyProduct("Trading Lab Desktop")]
[assembly: AssemblyCopyright("Copyright (C) 2026 Trading Lab Systems")]
[assembly: AssemblyVersion("1.9.11.0")]
[assembly: AssemblyFileVersion("1.9.11.0")]
[assembly: AssemblyInformationalVersion("1.9.11")]

namespace TradingLabPortable
{
    internal static class Program
    {
        private const string ProductVersion = "1.9.11";
        private const string PayloadResource = "TradingLab.payload.zip";
        private const string SingleInstanceName = "Local\\TradingLabDesktop.SingleInstance";

        [DllImport("user32.dll")]
        private static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        private static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);

        [STAThread]
        private static int Main(string[] args)
        {
            bool createdNew;
            using (Mutex singleInstance = new Mutex(true, SingleInstanceName, out createdNew))
            {
                if (!createdNew)
                {
                    BringExistingWindowToFront();
                    MessageBox.Show(
                        "O Trading Lab já está aberto. A janela existente foi mantida ativa.",
                        "Trading Lab Desktop",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Information);
                    return 0;
                }
                return RunSingleInstance(args);
            }
        }

        private static int RunSingleInstance(string[] args)
        {
            string extractionRoot = Path.Combine(
                Path.GetTempPath(),
                "TradingLab-v" + ProductVersion + "-" + Guid.NewGuid().ToString("N"));

            try
            {
                Directory.CreateDirectory(extractionRoot);
                string payloadPath = Path.Combine(extractionRoot, "payload.zip");
                ExtractPayload(payloadPath);
                ZipFile.ExtractToDirectory(payloadPath, extractionRoot);
                File.Delete(payloadPath);

                string executable = Path.Combine(extractionRoot, "TradingLab", "TradingLab.exe");
                if (!File.Exists(executable))
                {
                    throw new FileNotFoundException("O executável interno não foi encontrado.", executable);
                }

                ProcessStartInfo startInfo = new ProcessStartInfo(executable);
                startInfo.WorkingDirectory = Path.GetDirectoryName(executable);
                startInfo.UseShellExecute = false;
                startInfo.Arguments = JoinArguments(args);

                using (Process process = Process.Start(startInfo))
                {
                    process.WaitForExit();
                    return process.ExitCode;
                }
            }
            catch (Exception error)
            {
                MessageBox.Show(
                    "Não foi possível iniciar o Trading Lab v" + ProductVersion + ".\n\n" + error.Message,
                    "Trading Lab Desktop",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return 1;
            }
            finally
            {
                TryDeleteDirectory(extractionRoot);
            }
        }

        private static void BringExistingWindowToFront()
        {
            foreach (Process process in Process.GetProcessesByName("TradingLab"))
            {
                try
                {
                    if (process.MainWindowHandle != IntPtr.Zero)
                    {
                        ShowWindowAsync(process.MainWindowHandle, 9);
                        SetForegroundWindow(process.MainWindowHandle);
                        return;
                    }
                }
                catch
                {
                    // A process can finish while Windows is enumerating it.
                }
            }
        }

        private static void ExtractPayload(string destination)
        {
            Assembly assembly = Assembly.GetExecutingAssembly();
            using (Stream input = assembly.GetManifestResourceStream(PayloadResource))
            {
                if (input == null)
                {
                    throw new InvalidDataException("O pacote interno do aplicativo está ausente.");
                }

                using (FileStream output = File.Create(destination))
                {
                    input.CopyTo(output);
                }
            }
        }

        private static string JoinArguments(string[] args)
        {
            StringBuilder result = new StringBuilder();
            foreach (string argument in args)
            {
                if (result.Length > 0)
                {
                    result.Append(' ');
                }
                result.Append(QuoteArgument(argument));
            }
            return result.ToString();
        }

        private static string QuoteArgument(string argument)
        {
            if (argument.Length > 0 && argument.IndexOfAny(new[] { ' ', '\t', '"' }) < 0)
            {
                return argument;
            }

            StringBuilder quoted = new StringBuilder("\"");
            int backslashes = 0;
            foreach (char character in argument)
            {
                if (character == '\\')
                {
                    backslashes++;
                    continue;
                }

                if (character == '"')
                {
                    quoted.Append('\\', backslashes * 2 + 1);
                    quoted.Append('"');
                    backslashes = 0;
                    continue;
                }

                quoted.Append('\\', backslashes);
                backslashes = 0;
                quoted.Append(character);
            }
            quoted.Append('\\', backslashes * 2);
            quoted.Append('"');
            return quoted.ToString();
        }

        private static void TryDeleteDirectory(string path)
        {
            try
            {
                if (Directory.Exists(path))
                {
                    Directory.Delete(path, true);
                }
            }
            catch
            {
                // A pasta temporária pode permanecer se o Windows ainda mantiver uma DLL aberta.
            }
        }
    }
}
