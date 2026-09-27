
import React from 'react';
import { 
    Cloud, 
    ArrowRight, 
    PlayCircle,
    Check,
    Menu,
    Box,
    Wind,
    Activity,
    MousePointer2,
    Database,
    Cpu,
    Zap
} from 'lucide-react';
import './LandingPage.css';

const LandingPage = ({ onStart }: { onStart: () => void }) => {
    return (
        <div className="landing-page">
            {/* Navbar */}
            <nav className="navbar">
                <a href="#" className="logo">
                    <Cloud className="w-8 h-8 text-blue-600" />
                    <span>智仿云 AI</span>
                </a>
                
                <ul className="nav-links hidden md:flex">
                    <li><a href="#product">产品</a></li>
                    <li><a href="#solutions">解决方案</a></li>
                    <li><a href="#resources">资源中心</a></li>
                    <li><a href="#pricing">价格</a></li>
                </ul>

                <div className="nav-actions">
                    <button className="btn btn-text">登录</button>
                    <button className="btn btn-primary" onClick={onStart}>立即开始仿真</button>
                </div>
            </nav>

            {/* Hero Section */}
            <section className="hero">
                <div className="container">
                    <div className="hero-grid">
                        <div className="hero-content">
                            <h1>云原生 AI 工程仿真平台</h1>
                            <p>
                                智仿云 AI 通过统一的浏览器端平台，让团队在数秒内探索成千上万个工程决策，从而加速创新。
                            </p>
                            <div className="hero-actions">
                                <button className="btn btn-primary btn-lg" onClick={onStart}>
                                    立即开始仿真
                                </button>
                                <button className="btn btn-outline btn-lg">
                                    <PlayCircle size={20} /> 交互式演示
                                </button>
                            </div>
                            
                            <div className="tech-stack">
                                <span className="tech-text">核心技术驱动</span>
                                <div className="tech-tags">
                                    <span className="tech-tag">WebGL</span>
                                    <span className="tech-tag">Three.js</span>
                                    <span className="tech-tag">React</span>
                                    <span className="tech-tag">Cloud Native</span>
                                    <span className="tech-tag">AI Large Models</span>
                                </div>
                            </div>
                        </div>

                        {/* Visual: Mockup UI showing simulation result */}
                        <div className="hero-visual">
                            <div className="mockup-window">
                                <div className="mockup-header">
                                    <div className="window-dots">
                                        <div className="dot red"></div>
                                        <div className="dot yellow"></div>
                                        <div className="dot green"></div>
                                    </div>
                                    <div className="address-bar">
                                        simcloud.ai/工作台/项目-阿尔法
                                    </div>
                                </div>
                                <div className="mockup-body">
                                    <div className="mockup-sidebar">
                                        <div className="sidebar-icon active"></div>
                                        <div className="sidebar-icon"></div>
                                        <div className="sidebar-icon"></div>
                                        <div className="sidebar-icon"></div>
                                    </div>
                                    <div className="mockup-viewport">
                                        {/* CSS-based Simulation Visualization */}
                                        <div className="sim-visualization"></div>
                                        <div className="sim-model-wireframe"></div>
                                        <div className="sim-legend"></div>
                                        
                                        <div className="sim-controls">
                                            <button className="control-btn"></button>
                                            <button className="control-btn"></button>
                                            <button className="control-btn"></button>
                                        </div>

                                        {/* Floating Label */}
                                        <div style={{
                                            position: 'absolute', 
                                            top: '20px', 
                                            right: '20px', 
                                            background: 'rgba(255,255,255,0.9)', 
                                            padding: '8px 12px', 
                                            borderRadius: '6px',
                                            fontSize: '12px',
                                            fontWeight: 'bold',
                                            color: '#333',
                                            boxShadow: '0 4px 12px rgba(0,0,0,0.1)'
                                        }}>
                                            <span style={{color: '#0070c0'}}>●</span> 速度幅值 (m/s)
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            {/* Feature Cards Section (SimScale Style) */}
            <section className="section-features" id="solutions">
                <div className="container">
                    <div className="section-header">
                        <h2>统一仿真数据</h2>
                        <p>一个平台搞定所有物理场。从 CFD 到 FEA，在云端管理您的整个仿真生命周期。</p>
                    </div>

                    <div className="features-grid">
                        {/* Card 1: Cloud Simulation */}
                        <div className="feature-card">
                            <div className="card-image">
                                <div className="pattern-cfd"></div>
                                <div style={{position: 'absolute', bottom: '-20px', right: '-20px'}}>
                                    <Wind size={120} color="#0070c0" opacity={0.1} />
                                </div>
                            </div>
                            <div className="card-content">
                                <h3>云端仿真</h3>
                                <p>智仿云通过简单易用、即时访问的 Web 界面，将强大的多物理场仿真能力交到每一位工程师手中。</p>
                                <a href="#" className="learn-more">了解更多 <ArrowRight size={16} /></a>
                            </div>
                        </div>

                        {/* Card 2: Engineering AI */}
                        <div className="feature-card">
                            <div className="card-image">
                                <div className="pattern-fea"></div>
                                <div style={{position: 'absolute', top: '20px', left: '20px'}}>
                                    <Cpu size={80} color="#0070c0" opacity={0.2} />
                                </div>
                            </div>
                            <div className="card-content">
                                <h3>工程 AI</h3>
                                <p>Agentic AI 编排仿真的设置、执行、评估和文档编制。AI 智能体可自主运行 CAE 工作流。</p>
                                <a href="#" className="learn-more">了解更多 <ArrowRight size={16} /></a>
                            </div>
                        </div>

                        {/* Card 3: Physics AI */}
                        <div className="feature-card">
                            <div className="card-image">
                                <div className="pattern-cad"></div>
                                <div style={{position: 'absolute', bottom: '20px', right: '20px'}}>
                                    <Zap size={80} color="#f59e0b" opacity={0.2} />
                                </div>
                            </div>
                            <div className="card-content">
                                <h3>物理 AI</h3>
                                <p>利用与高保真仿真关联的 AI 模型，即时预测物理行为。利用历史 CAE 数据实现即时优化。</p>
                                <a href="#" className="learn-more">了解更多 <ArrowRight size={16} /></a>
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            {/* Simulation Showcase Section */}
            <section className="section-showcase" id="showcase">
                <div className="container">
                    <div className="section-header">
                        <h2>高精度仿真场景展示</h2>
                        <p>覆盖流体、结构、热力学等多物理场，提供从建模到结果可视化的全流程体验。</p>
                    </div>

                    <div className="showcase-grid">
                        {/* Case 1: Aerodynamics */}
                        <div className="showcase-card">
                            <div className="showcase-image aero-bg">
                                <div className="overlay-content">
                                    <div className="badge">CFD 流体动力学</div>
                                    <h3>航空翼型气动分析</h3>
                                    <p>基于 RANS 方程的湍流模型求解，实时计算升阻力系数。</p>
                                </div>
                            </div>
                        </div>

                        {/* Case 2: Structural Analysis */}
                        <div className="showcase-card">
                            <div className="showcase-image struct-bg">
                                <div className="overlay-content">
                                    <div className="badge">FEA 有限元分析</div>
                                    <h3>机械支架应力分布</h3>
                                    <p>高精度网格划分，Von Mises 应力云图实时渲染。</p>
                                </div>
                            </div>
                        </div>

                        {/* Case 3: Thermal Simulation */}
                        <div className="showcase-card">
                            <div className="showcase-image thermal-bg">
                                <div className="overlay-content">
                                    <div className="badge">热力学分析</div>
                                    <h3>电子散热器温场模拟</h3>
                                    <p>耦合传热分析，优化散热结构设计。</p>
                                </div>
                            </div>
                        </div>
                         {/* Case 4: Large Scale Assembly */}
                         <div className="showcase-card wide">
                            <div className="showcase-image assembly-bg">
                                <div className="overlay-content">
                                    <div className="badge">大规模装配体</div>
                                    <h3>整车空气动力学优化</h3>
                                    <p>支持千万级网格规模，云端并行计算加速。</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            {/* Footer */}
            <footer className="footer">
                <div className="container">
                    <div className="footer-top">
                        <div className="footer-col">
                            <a href="#" className="logo" style={{marginBottom: '20px', display: 'inline-flex'}}>
                                <Cloud className="w-6 h-6 text-blue-600" />
                                <span>智仿云 AI</span>
                            </a>
                            <p style={{color: '#718096', maxWidth: '300px'}}>
                                智仿云 AI 是专为 AI 时代设计的全球首个云原生工程仿真平台。
                            </p>
                        </div>
                        <div className="footer-col">
                            <h4>产品</h4>
                            <ul>
                                <li><a href="#">流体动力学 (CFD)</a></li>
                                <li><a href="#">有限元分析 (FEA)</a></li>
                                <li><a href="#">热分析</a></li>
                            </ul>
                        </div>
                        <div className="footer-col">
                            <h4>公司</h4>
                            <ul>
                                <li><a href="#">关于我们</a></li>
                                <li><a href="#">加入我们</a></li>
                                <li><a href="#">客户案例</a></li>
                            </ul>
                        </div>
                        <div className="footer-col">
                            <h4>资源</h4>
                            <ul>
                                <li><a href="#">文档</a></li>
                                <li><a href="#">社区论坛</a></li>
                                <li><a href="#">博客</a></li>
                            </ul>
                        </div>
                    </div>
                    <div className="footer-bottom">
                        <p>&copy; 2026 智仿云 AI. 版权所有。 隐私政策 | 服务条款</p>
                    </div>
                </div>
            </footer>
        </div>
    );
};

export default LandingPage;
