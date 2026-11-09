using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

internal static class SetupGuide
{
    [STAThread]
    private static void Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        try
        {
            var start = new ProcessStartInfo();
            start.FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), @"WindowsPowerShell\v1.0\powershell.exe");
            start.Arguments = "-NoLogo -NoProfile -STA -ExecutionPolicy Bypass -File \"" + Path.Combine(root, "open-guide.ps1") + "\"";
            start.UseShellExecute = false;
            // Suppress the console, not the actual setup window.
            start.CreateNoWindow = true;
            start.WorkingDirectory = root;
            Process.Start(start);
        }
        catch (Exception error)
        {
            MessageBox.Show("Could not open ClipGo setup. Open welcome.html in the installation folder for manual instructions.\n\n" + error.Message,
                "ClipGo Setup", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}