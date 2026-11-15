using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

namespace MSimStudio
{
    public class StudioForm : Form
    {
        private TextBox txtOrder;
        private TextBox txtFc;
        private TextBox txtCodeOut;
        private TextBox txtImgIn;
        private TextBox txtVisOut;
        private TextBox txtLog;
        private string repoRoot;

        public StudioForm(string rootDir)
        {
            repoRoot = rootDir;
            Text = "MSimStudio Native EXE - NI Multisim 14 (.ms14) Code & Vision Synthesizer";
            Size = new Size(820, 560);
            StartPosition = FormStartPosition.CenterScreen;
            Font = new Font("Segoe UI", 9.5f);

            TabControl tabs = new TabControl { Dock = DockStyle.Top, Height = 260 };
            TabPage tabCode = new TabPage("Mode 1: Code / Parametric Synthesizer");
            TabPage tabVis = new TabPage("Mode 2: Image-to-Multisim (Vision OCR)");

            Label lblOrder = new Label { Text = "Filter Order (3-11):", Left = 20, Top = 25, Width = 160 };
            txtOrder = new TextBox { Text = "7", Left = 190, Top = 22, Width = 140 };

            Label lblFc = new Label { Text = "Cutoff Freq fc (Hz):", Left = 20, Top = 65, Width = 160 };
            txtFc = new TextBox { Text = "12000", Left = 190, Top = 62, Width = 140 };

            Label lblOut1 = new Label { Text = "Output .ms14 Path:", Left = 20, Top = 105, Width = 160 };
            txtCodeOut = new TextBox { Text = Path.Combine(repoRoot, "dist", "Studio_Butterworth_7th.ms14"), Left = 190, Top = 102, Width = 560 };

            Button btnRunCode = new Button { Text = "Synthesize Symmetrical .ms14 Circuit", Left = 190, Top = 150, Width = 280, Height = 38 };
            btnRunCode.Click += (s, e) => RunPython(string.Format("-m msim_compiler --filter-order {0} --filter-fc {1} --out \"{2}\"", txtOrder.Text, txtFc.Text, txtCodeOut.Text));

            tabCode.Controls.AddRange(new Control[] { lblOrder, txtOrder, lblFc, txtFc, lblOut1, txtCodeOut, btnRunCode });

            Label lblImg = new Label { Text = "Input Circuit Image:", Left = 20, Top = 25, Width = 160 };
            txtImgIn = new TextBox { Text = Path.Combine(repoRoot, "dist", "sample_input_circuit.png"), Left = 190, Top = 22, Width = 450 };
            Button btnBrowse = new Button { Text = "Browse...", Left = 650, Top = 20, Width = 100, Height = 28 };
            btnBrowse.Click += (s, e) => {
                OpenFileDialog ofd = new OpenFileDialog { Filter = "Images|*.png;*.jpg;*.jpeg;*.bmp" };
                if (ofd.ShowDialog() == DialogResult.OK) txtImgIn.Text = ofd.FileName;
            };

            Label lblOut2 = new Label { Text = "Output .ms14 Path:", Left = 20, Top = 65, Width = 160 };
            txtVisOut = new TextBox { Text = Path.Combine(repoRoot, "dist", "vision_synthesized_circuit.ms14"), Left = 190, Top = 62, Width = 560 };

            Button btnRunVis = new Button { Text = "Extract Image Topology & Compile .ms14", Left = 190, Top = 115, Width = 320, Height = 38 };
            btnRunVis.Click += (s, e) => RunPython(string.Format("-m msim_compiler --image \"{0}\" --out \"{1}\"", txtImgIn.Text, txtVisOut.Text));

            tabVis.Controls.AddRange(new Control[] { lblImg, txtImgIn, btnBrowse, lblOut2, txtVisOut, btnRunVis });

            tabs.TabPages.Add(tabCode);
            tabs.TabPages.Add(tabVis);

            txtLog = new TextBox {
                Multiline = true,
                ReadOnly = true,
                ScrollBars = ScrollBars.Vertical,
                Dock = DockStyle.Fill,
                BackColor = Color.FromArgb(20, 24, 30),
                ForeColor = Color.FromArgb(180, 240, 200),
                Font = new Font("Consolas", 9.5f)
            };

            Controls.Add(txtLog);
            Controls.Add(tabs);
        }

        private void RunPython(string args)
        {
            txtLog.AppendText("[EXEC] python " + args + Environment.NewLine);
            ProcessStartInfo psi = new ProcessStartInfo("python", args) {
                WorkingDirectory = repoRoot,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            };
            psi.EnvironmentVariables["PYTHONPATH"] = repoRoot;
            Process p = Process.Start(psi);
            string stdout = p.StandardOutput.ReadToEnd();
            string stderr = p.StandardError.ReadToEnd();
            p.WaitForExit();
            if (!string.IsNullOrEmpty(stdout)) txtLog.AppendText(stdout + Environment.NewLine);
            if (!string.IsNullOrEmpty(stderr)) txtLog.AppendText(stderr + Environment.NewLine);
        }

        [STAThread]
        public static void Main(string[] args)
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string root = Directory.GetParent(baseDir.TrimEnd('\\', '/')).FullName;
            if (!Directory.Exists(Path.Combine(root, "msim_compiler"))) root = Directory.GetCurrentDirectory();
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new StudioForm(root));
        }
    }
}
